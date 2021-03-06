import functools
import logging
import os

import flask
import sentry_sdk
from flask import Flask, Request, render_template, request, url_for
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration
from werkzeug.utils import redirect

from overtrack_web.data import CDN_URL, WELCOME_META
from overtrack_web.lib.authentication import check_authentication

# port of https://bugs.python.org/issue34363 to the dataclasses backport
# see https://github.com/ericvsmith/dataclasses/issues/151
from overtrack_web.lib import dataclasses_asdict_namedtuple_patch
dataclasses_asdict_namedtuple_patch.patch()

request: Request = request

try:
    # Fancy logging when possible
    from overtrack.util.logging_config import config_logger
    config_logger(__name__, logging.INFO, False)
except ImportError:
    logging.basicConfig(level=logging.INFO)


# ------ FLASK SETUP AND CONFIG ------
app = Flask(__name__, template_folder='../templates', static_folder='../static')
app.url_map.strict_slashes = False

@app.after_request
def add_default_no_cache_header(response):
    # response.cache_control.no_store = True
    if 'cache-control' not in response.headers:
        response.headers['cache-control'] = 'no-store'
    return response


# ------ FLASK DEBUG vs. LIVE CONFIG ------
if app.config['DEBUG']:
    # live building of scss
    from sassutils.wsgi import SassMiddleware, Manifest
    app.wsgi_app = SassMiddleware(
        app.wsgi_app,
        {
            'overtrack_web': Manifest(
                '../static/scss',
                '../static/css',
                '/static/css',
                strip_extension=True,
            )
        }
    )

    # Fake login for dev
    from overtrack_web.views.fake_login import fake_login_blueprint
    app.register_blueprint(fake_login_blueprint, url_prefix='/fake_login')

else:
    sentry_sdk.init(
        os.environ.get('SENTRY_DSN', 'https://077ec8ffb4404ce384ab84a5e6bc17ae@sentry.io/1450230'),
        integrations=[
            AwsLambdaIntegration()
        ],
        with_locals=True,
        debug=False
    )

    # Set up exception handling for running on lambda
    orig_handle_exception = app.handle_exception
    def handle_exception(e):
        sentry_sdk.capture_exception(e)
        return orig_handle_exception(e)
    app.handle_exception = handle_exception
    def unhandled_exceptions(e, event, context):
        sentry_sdk.capture_exception(e)
        return True

    # Fetch static assets from cdn instead of through the lambda
    orig_url_for = flask.url_for
    def url_for(endpoint, **values):
        if endpoint == 'static' and 'filename' in values:
            return CDN_URL + '/' + values['filename']
        else:
            return orig_url_for(endpoint, **values)
    app.jinja_env.globals['url_for'] = url_for
    flask.url_for = url_for


# ------ JINJA2 TEMPLATE VARIABLES AND FILTERS ------
@app.context_processor
def context_processor():
    from overtrack_web.lib.context_processors import processors as lib_context_processors
    from overtrack_web.lib.session import session
    processors = dict(lib_context_processors)
    processors['user'] = session.user if check_authentication() is None else None
    return processors
from overtrack_web.lib.template_filters import filters
app.jinja_env.filters.update(filters)


# ------ LOGIN/LOGOUT ------
from overtrack_web.views.login import login_blueprint
app.register_blueprint(login_blueprint)


# ------ APEX ------
from overtrack_web.views.apex.games_list import games_list_blueprint as apex_games_list_blueprint
app.register_blueprint(apex_games_list_blueprint, url_prefix='/apex/games')

from overtrack_web.views.apex.game import game_blueprint
app.register_blueprint(game_blueprint, url_prefix='/apex/games')

from overtrack_web.views.apex.stats import results_blueprint
app.register_blueprint(results_blueprint, url_prefix='/apex/stats')

from overtrack_web.views.apex.scrims import scrims_blueprint
app.register_blueprint(scrims_blueprint, url_prefix='/apex/scrims')

try:
    # support running even if the discord bot fails (e.g. missing env vars, fails to fetch cache of enabled bots)
    from overtrack_web.views.apex.discord_bot import discord_bot_blueprint
except:
    logging.exception('Failed to import discord_bot_blueprint - running without /discord_bot')
else:
    app.register_blueprint(discord_bot_blueprint, url_prefix='/apex/discord_bot')


# ------ OVERWATCH ------
from overtrack_web.views.overwatch.games_list import games_list_blueprint as overwatch_games_list_blueprint
app.register_blueprint(overwatch_games_list_blueprint, url_prefix='/overwatch/games')

from overtrack_web.views.overwatch.game import game_blueprint as overwatch_game_blueprint
app.register_blueprint(overwatch_game_blueprint, url_prefix='/overwatch/games')


# ------ LEGACY PAGE REDIRECTS ------
@app.route('/game/<path:key>')
def game_redirect(key):
    from overtrack_models.orm.overwatch_game_summary import OverwatchGameSummary
    try:
        OverwatchGameSummary.get(key)
    except OverwatchGameSummary.DoesNotExist:
        return redirect(url_for('apex.game.game', key=key), code=308)
    else:
        return redirect(url_for('overwatch.game.game', key=key), code=308)

@app.route('/games/<string:key>')
def overwatch_share_link_redirect(key):
    return redirect(url_for('overwatch.games_list.shared_games_list', sharekey=key), code=308)

# redirect old apex.overtrack.gg/<streamer> shares
for key, username in {
    'mendokusaii': 'mendokusaii',
}.items():
    app.add_url_rule(
        f'/{key}',
        f'hardcoded_redirect_{key}',
        functools.partial(redirect, f'/apex/games/{username}', code=308)
    )

@app.route('/apex')
@app.route('/games')
def apex_games_redirect():
    return redirect(url_for('apex.games_list.games_list'), code=308)


# ------ SUBSCRIBE  ------
try:
    from overtrack_web.views.subscribe import subscribe_blueprint
except:
    logging.exception('Failed to import subscribe_blueprint - running without /subscribe')
else:
    app.register_blueprint(subscribe_blueprint, url_prefix='/subscribe')


# ------ ROOT PAGE  ------
@app.route('/')
def root():
    if check_authentication() is None:
        return redirect(url_for('apex.games_list.games_list'), code=307)
    else:
        return welcome()


# ------ SIMPLE INFO PAGES  ------
@app.route('/client')
def client():
    return render_template('client.html', meta=WELCOME_META)

@app.route('/welcome')
def welcome():
    return render_template('welcome.html', meta=WELCOME_META)

@app.route('/discord')
def discord_redirect():
    return redirect('https://discord.gg/JywstAB')
