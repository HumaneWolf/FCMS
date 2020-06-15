import json
import os
import smtplib
import urllib
from binascii import hexlify
from datetime import datetime, timedelta, tzinfo
from urllib.parse import urljoin

from pyramid.view import view_config
import pyramid.httpexceptions as exc
from pyramid.security import remember, forget
from ..models import user, carrier, ResetToken
from ..utils import capi, sapi, util
from ..utils.encryption import pwd_context
import logging

log = logging.getLogger(__name__)


@view_config(route_name='forgot-password', renderer='../templates/forgot_password.jinja2')
def resetpass_view(request):
    if request.POST:
        print(f"Got a post: {request.POST}")
    if 'token' in request.POST:
        print("Token in param.")
        if request.params['password'] != request.params['password_verify']:
            log.warning(f"Password reset attempt - passwords do not match.")
            return {'error': 'Passwords do not match!'}
        log.warning(f"Reset password attempt by {request.client_addr} for token {request.POST['token']}")
        resettoken = request.dbsession.query(ResetToken).filter(ResetToken.token == request.POST['token']).one_or_none()
        if resettoken:
            if resettoken.token == request.POST['token']:
                log.debug(f"Valid token supplied.")
                if resettoken.expires_at < datetime.utcnow():
                    log.error(f"Reset token expired, not valid. {resettoken.expires_at}")
                else:
                    myuser = request.dbsession.query(user.User).filter(user.User.id == resettoken.user_id).one_or_none()
                    if myuser:
                        log.warning(f"Successful reset by {request.client_addr}")
                        cryptpass = pwd_context.hash(request.POST['password'])
                        myuser.password = cryptpass
                        request.dbsession.query(ResetToken).filter(ResetToken.user_id == myuser.id).delete()
                        request.dbsession.flush()
                        request.dbsession.refresh(myuser)
                        return {'reset_success': 'Password updated!'}
                    else:
                        log.critical(f"User does not exist for valid reset token?!! This should not happen.")
        else:
            log.error(f"Invalid token supplied by {request.client_addr}")
            return {'error': 'Invalid password reset token.'}
    elif 'request_token' in request.POST:
        print("Got request_token")
        if 'email' not in request.POST or request.POST['email'] == '':
            return {'error': 'No email supplied'}
        myuser = request.dbsession.query(user.User).filter(user.User.username == request.POST['email']).one_or_none()
        if myuser:
            # Send email.
            print("Got a valid email to send.")
            request.dbsession.query(ResetToken).filter(ResetToken.user_id == myuser.id).delete()
            token = hexlify(os.urandom(64)).decode()
            tc = ResetToken(user_id=myuser.id, token=token, expires_at=datetime.now() + timedelta(hours=1),
                            generated_by=request.client_addr)
            request.dbsession.add(tc)
            request.dbsession.flush()
            request.dbsession.refresh(tc)
            url = urljoin(request.route_url('forgot-password'), f'?token={tc.token}')
            util.send_email(myuser.username, 'Password reset for Fleetcarrier.space',
                            f'Greetings CMDR!\n\nSomeone (hopefully you!) has requested a password reset for your '
                            f'account. You can reset your password by clicking the link below.\n\n{url}')
        else:
            print("Invalid email, but ignore.")
        return {'view': 'reset-password', 'email_sent': True}
    return {'project': 'Fleet Carrier Management System'}


@view_config(route_name='login', renderer='../templates/login.jinja2')
def login_view(request):
    if 'test' not in request.session:
        request.session['test'] = True
    if 'email' in request.params:
        res = request.dbsession.query(user.User).filter(user.User.username == request.params['email']).one_or_none()
        if res:
            if pwd_context.verify(request.params['pass'], res.password):
                if request.params['remember']:
                    headers = remember(request, res.id, max_age=2629800)
                else:
                    headers = remember(request, res.id, max_age=3600)
                return exc.HTTPFound('/my_carrier', headers=headers)
            else:
                log.warning(f"Failed login for {request.params['email']} from {request.client_addr}.")
        else:
            log.warning(f"Attempt to log in to non-existing user {request.params['email']} from {request.client_addr}")
    return {'project': 'Fleet Carrier Management System', 'view': 'Login'}


@view_config(route_name='logout', renderer='../templates/login.jinja2')
def logout_view(request):
    headers = forget(request)
    return exc.HTTPFound(location=request.route_url('home'), headers=headers)


@view_config(route_name='register', renderer='../templates/register.jinja2')
def register_view(request):
    if 'register' in request.params:
        if not request.params['email']:
            return {'reg_failure': True, 'message': 'No email set.'}
        if not request.params['cmdr_name']:
            return {'reg_failure': True, 'message': 'No CMDR name set.'}
        if request.params['pass'] != request.params['pass_verify']:
            return {'reg_failure': True, 'message': 'Passwords do not match.'}
        res = request.dbsession.query(user.User).filter(user.User.username == request.params['email']).one_or_none()
        if res:
            return {'reg_failure': True, 'message': 'User exists!'}
        cryptpass = pwd_context.hash(request.params['pass'])
        apikey = hexlify(os.urandom(64)).decode()
        newuser = user.User(username=request.params['email'], password=cryptpass, userlevel=1,
                            cmdr_name=request.params['cmdr_name'], has_validated=False, public_carrier=True,
                            banned=False, apiKey=apikey)
        request.dbsession.add(newuser)
        log.info(f"Registered new user {request.params['email']} from {request.client_addr}.")
        return exc.HTTPFound(location=request.route_url('login'))
    return {'project': 'Fleet Carrier Management System'}


@view_config(route_name='oauth', renderer='../templates/register.jinja2')
def oauth_view(request):
    url, state = capi.get_auth_url()
    return exc.HTTPFound(location=url)


@view_config(route_name='oauth_callback', renderer='../templates/register.jinja2')
def oauth_callback(request):
    user = request.user
    if not user:
        log.warning(f"Attempt to call Oauth flow without login from {request.client_addr}")
        return {'project': 'Error: You should be logged in before completing Oauth!'}
    state = request.params['state']
    token = capi.get_token(request.url, state=state)
    print(token)
    user.access_token = str(dict(token))
    user.refresh_token = token['refresh_token']
    user.token_expiration = token['expires_at']
    log.info(f"User {user.username} has completed OAuth authentication.")
    return exc.HTTPFound(location=request.route_url('oauth_finalize'))


@view_config(route_name='oauth_finalize', renderer='../templates/login.jinja2')
def oauth_finalize(request):
    user = request.user
    try:
        jcarrier = capi.get_carrier(user)
        services = jcarrier['market']['services']
        oc = request.dbsession.query(carrier.Carrier).filter(
            carrier.Carrier.callsign == jcarrier['name']['callsign']).one_or_none()
        if oc:
            log.warning(f"User {user.username} completed OAuth, but we already have their carrier. Update it.")
            if oc.owner != user.id:
                log.warning(f"Carrier {oc.callsign} had no owner, setting it.")
                oc.owner = user.id
            return {'project': 'Oauth complete. Redirecting you to carrier homepage.',
                    'meta': {'refresh': True, 'target': request.route_url('/my_carrier'), 'delay': 5}}
        coords = sapi.get_coords(jcarrier['currentStarSystem'])
        if not coords:
            coords = {"x": 0, "y": 0, "z": 0}
        newcarrier = carrier.Carrier(owner=user.id, callsign=jcarrier['name']['callsign'],
                                     name=jcarrier['name']['vanityName'],
                                     currentStarSystem=jcarrier['currentStarSystem'], balance=jcarrier['balance'],
                                     fuel=jcarrier['fuel'], state=jcarrier['state'], theme=jcarrier['theme'],
                                     dockingAccess=jcarrier['dockingAccess'],
                                     notoriousAccess=jcarrier['notoriousAccess'],
                                     totalDistanceJumped=jcarrier['itinerary']['totalDistanceJumpedLY'],
                                     currentJump=jcarrier['itinerary']['currentJump'],
                                     taxation=jcarrier['finance']['taxation'], coreCost=jcarrier['finance']['coreCost'],
                                     servicesCost=jcarrier['finance']['servicesCost'],
                                     jumpsCost=jcarrier['finance']['jumpsCost'],
                                     numJumps=jcarrier['finance']['numJumps'], hasCommodities=True,
                                     hasCarrierFuel=True,
                                     hasRearm=True if services['rearm'] == 'ok' else False,
                                     hasRepair=True if services['repair'] == 'ok' else False,
                                     hasRefuel=True if services['refuel'] == 'ok' else False,
                                     hasShipyard=True if services['shipyard'] == 'ok' else False,
                                     hasOutfitting=True if services['outfitting'] == 'ok' else False,
                                     hasBlackMarket=True if services['blackmarket'] == 'ok' else False,
                                     hasVoucherRedemption=True if services['voucherredemption'] == 'ok' else False,
                                     hasExploration=True if services['exploration'] == 'ok' else False,
                                     cachedJson=json.dumps(jcarrier),
                                     x=coords['x'],
                                     y=coords['y'],
                                     z=coords['z'],
                                     trackedOnly=False,
                                     lastUpdated=datetime.now())
        user.no_carrier = False
        request.dbsession.add(newcarrier)
        request.dbsession.flush()
        request.dbsession.refresh(newcarrier)
        user.carrierid = newcarrier.id
        # TODO: Inject rest of the data too.
        # TODO: Redirect to my_carrier after delay.
    except:
        user.no_carrier = True
        return {'project': 'Failed to retrieve your carrier. But no worries, you can still use our site! '
                           'If you purchase one, go to your /my_carrier page and click the button there, '
                           'and we will add it!', 'meta': {'refresh': True, 'target': '/my_carrier', 'delay': 5}}
    return {'project': 'OAuth flow completed. Carrier added.',
            'meta': {'refresh': True, 'target': 'my_carrier', 'delay': 5}}
