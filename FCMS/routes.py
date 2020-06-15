def includeme(config):
    config.add_static_view('static', 'static', cache_max_age=3600)
    config.add_route('home', '/')
    config.add_route('login', '/login')
    config.add_route('logout', '/logout')
    config.add_route('register', '/register')
    config.add_route('oauth_callback', '/oauth/callback')
    config.add_route('oauth', '/oauth')
    config.add_route('my_carrier', '/my_carrier')
    config.add_route('carrier', '/carrier/{cid}')
    config.add_route('carrier_subview', '/carrier/{cid}/{subview}')
    config.add_route('oauth_finalize', '/oauth/finalize')
    config.add_route('loadout', '/loadout')
    config.add_route('search', '/search')
    config.add_route('uploadtest', '/uploadtest')
    config.add_route('settings', '/settings')
    config.add_route('terms', '/terms')
    config.add_route('api', '/api')
    config.add_route('forgot-password', '/forgot-password')
