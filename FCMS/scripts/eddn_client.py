import zlib
import transaction
import zmq
import simplejson
import sys
import time
import os
import semver
import re

from pyramid.paster import (
    get_appsettings,
    setup_logging,
)

from pyramid.scripts.common import parse_vars
from sqlalchemy import func
from sqlalchemy.exc import DataError, IntegrityError

from FCMS.models import (
    get_engine,
    get_session_factory,
    get_tm_session, Market,
)
from FCMS.utils.eddn import process_eddn
from FCMS.models.carrier import Carrier

__relayEDDN = 'tcp://eddn.edcd.io:9500'
__replayEDDN = 'tcp://localhost:9500'

__timeoutEDDN = 600000
__scoopable = ['K', 'G', 'B', 'F', 'O', 'A', 'M']

__allowedSchema = [
    "https://eddn.edcd.io/schemas/journal/1",
    "https://eddn.edcd.io/schemas/commodity/1",
    "https://eddn.edcd.io/schemas/commodity/2",
    "https://eddn.edcd.io/schemas/commodity/3"
]

__blockedSoftware = [
    "ed-ibe (api)".casefold(),
    "ed central production server".casefold(),
    "eliteocr".casefold(),
    "regulatednoise__dj".casefold(),
    "ocellus - elite: dangerous assistant".casefold(),
    "eva".casefold()
]

BASEVERSION = re.compile(
    r"""[vV]?
        (?P<major>0|[1-9]\d*)
        (\.
        (?P<minor>0|[1-9]\d*)
        (\.
            (?P<patch>0|[1-9]\d*)
        )?
        )?
    """,
    re.VERBOSE,
)

carrier_rs = '^[A-Za-z0-9]{3}-[A-Za-z0-9]{3}$'
carrier_r = re.compile(carrier_rs)


def coerce(version):
    """
    Convert an incomplete version string into a semver-compatible VersionInfo
    object

    * Tries to detect a "basic" version string (``major.minor.patch``).
    * If not enough components can be found, missing components are
        set to zero to obtain a valid semver version.

    :param str version: the version string to convert
    :return: a tuple with a :class:`VersionInfo` instance (or ``None``
        if it's not a version) and the rest of the string which doesn't
        belong to a basic version.
    :rtype: tuple(:class:`VersionInfo` | None, str)
    """
    match = BASEVERSION.search(version)
    if not match:
        return version

    ver = {
        key: 0 if value is None else value
        for key, value in match.groupdict().items()
    }
    ver = str(semver.VersionInfo(**ver))
    return ver


def validsoftware(name, version):
    """
    Checks whether a EDDN actor is on our valid software list, and isn't a blocked version.
    :param name: Software name
    :param version: Version number
    :return:
    """
    if not name:
        return False
    if not version:
        return False
    ver = coerce(version)

    if name.casefold() == "e:d market connector".casefold():
        if semver.compare(ver, "2.4.9") < 0:
            print("Ignored old EDMC message.")
            return False
    if name.casefold() == "EDDiscovery".casefold():
        if semver.compare(ver, "9.1.1") < 0:
            print("Ignored old EDDiscovery message.")
            return False
    if name.casefold() == "EDDI".casefold():
        if semver.compare(ver, "2.4.5") < 0:
            print("Ignored old EDDI message.")
            return False
    if name.casefold() == "Moonlight".casefold():
        if semver.compare(ver, "1.3.4") < 0:
            print("Ignored old Moonlight message.")
            return False
    if name.casefold() in __blockedSoftware:
        print(f"Ignored blocked software {name}")
        return False
    return True


def get_count(q):
    count_q = q.statement.with_only_columns([func.count()]).order_by(None)
    count = q.session.execute(count_q).scalar()
    return count


def usage(argv):
    """
    Prints usage helpstring.
    :param argv: Args passed from system
    """
    cmd = os.path.basename(argv[0])
    print('usage: %s <config_uri> [var=value]\n'
          '(example: "%s development.ini")' % (cmd, cmd))
    sys.exit(1)


def main(argv=None):
    if argv is None:
        argv = sys.argv
    if len(argv) < 2:
        usage(argv)
    config_uri = argv[1]
    options = parse_vars(argv[2:])
    setup_logging(config_uri)
    settings = get_appsettings(config_uri, options=options)
    engine = get_engine(settings)
    session_factory = get_session_factory(engine)
    session = get_tm_session(session_factory, transaction.manager)

    context = zmq.Context()
    subscriber = context.socket(zmq.SUB)

    subscriber.setsockopt(zmq.SUBSCRIBE, b"")
    subscriber.setsockopt(zmq.RCVTIMEO, __timeoutEDDN)

    ncarcount = 0
    ucarcount = 0
    messages = 0
    totmsg = 0
    hmessages = 0
    mkupdates = 0
    rpmsg = 0
    print("Starting EDDN client.")
    while True:
        try:
            subscriber.connect(__relayEDDN)
            while True:
                __message = subscriber.recv()

                if not __message:
                    subscriber.disconnect(__relayEDDN)
                    break

                __message = zlib.decompress(__message)
                __json = simplejson.loads(__message)
                totmsg = totmsg + 1
                print(f"EDDN Client running. Messages: {messages:10} New carriers: {ncarcount:10} "
                      f"Updated carriers: {ucarcount:10}  Market updates: {mkupdates}\r",
                      end='')
                if validsoftware(__json['header']['softwareName'], __json['header']['softwareVersion']) \
                        and __json['$schemaRef'] in __allowedSchema:
                    hmessages = hmessages + 1
                    data = __json['message']
                    messages = messages + 1
                    upd = process_eddn(session, data)
                    mkupdates = mkupdates + upd['new_commodities']
                    ucarcount = ucarcount + upd['updated_carriers']
                    ncarcount = ncarcount + upd['new_carriers']
                sys.stdout.flush()

        except zmq.ZMQError as e:
            print('ZMQSocketException: ' + str(e))
            sys.stdout.flush()
            subscriber.disconnect(__relayEDDN)
            time.sleep(5)


if __name__ == '__main__':
    main()
