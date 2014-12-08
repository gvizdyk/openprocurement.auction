from flask_oauthlib.client import OAuth
from flask import Flask, request, jsonify, url_for, session
import os
from gevent.pywsgi import WSGIServer
from datetime import datetime
from pytz import timezone
from openprocurement.auction.forms import BidsForm
from openprocurement.auction.event_source import sse, send_event
from pytz import timezone as tz
from gevent import spawn, sleep


app = Flask(__name__, static_url_path='', template_folder='static')
app.auction_bidders = {}
app.register_blueprint(sse)
app.secret_key = os.urandom(24)


@app.route('/login')
def login():
    if 'remote_oauth' in session:
        resp = app.remote_oauth.get('allow_bid')
        return jsonify(resp.data)
    if 'bidder_id' in request.args:
        next_url = request.args.get('next') or request.referrer or None
        return app.remote_oauth.authorize(
            callback=url_for('authorized', next=next_url, _external=True),
            bidder_id=request.args['bidder_id']
        )
    return jsonify({"msg": "without bidder id"})


@app.route('/postbid', methods=['POST'])
def postBid():
    auction = app.config['auction']
    with auction.bids_actions:
        form = BidsForm.from_json(request.json)
        form.document = auction.db.get(auction.auction_doc_id)
        if form.validate():
            # write data
            current_time = datetime.now(timezone('Europe/Kiev'))
            auction.add_bid(form.document['current_stage'],
                            {'amount': request.json['bid'],
                             'bidder_id': request.json['bidder_id'],
                             'time': current_time.isoformat()})
            response = {'status': 'ok', 'data': request.json}
        else:
            response = {'status': 'failed', 'errors': form.errors}
        return jsonify(response)


@app.route('/authorized')
def authorized():
    resp = app.remote_oauth.authorized_response()
    if resp is None:
        return 'Access denied: reason=%s error=%s' % (
            request.args['error_reason'],
            request.args['error_description']
        )
    print resp
    session['remote_oauth'] = (resp['access_token'], '')
    return jsonify(oauth_token=resp['access_token'])


def push_timestamps_events(app):
    with app.app_context():
        while True:
            sleep(5)
            time = datetime.now(app.config['timezone']).isoformat()
            for bidder_id in app.auction_bidders:
                send_event(bidder_id, {"time": time}, "Tick")


def check_clients(app):
    with app.app_context():
        while True:
            sleep(30)

            for bidder_id in app.auction_bidders:
                removed_clients = []
                for client in app.auction_bidders[bidder_id]["channels"]:
                    if app.auction_bidders[bidder_id]["channels"][client].qsize() > 3:
                        removed_clients.append(client)
                if removed_clients:
                    for client in removed_clients:
                        del app.auction_bidders[bidder_id]["channels"][client]
                        del app.auction_bidders[bidder_id]["clients"][client]
                    send_event(
                        bidder_id,
                        app.auction_bidders[bidder_id]["clients"],
                        "ClientsList"
                    )


def run_server(auction, timezone='Europe/Kiev'):
    app.config.update(auction.worker_defaults)
    app.config['auction'] = auction
    app.config['timezone'] = tz(timezone)
    app.config['SESSION_COOKIE_PATH'] = '/tenders/{}'.format(auction.auction_doc_id)
    app.oauth = OAuth(app)

    app.remote_oauth = app.oauth.remote_app(
        'remote',
        consumer_key=app.config['OAUTH_CLIENT_ID'],
        consumer_secret=app.config['OAUTH_CLIENT_SECRET'],
        request_token_params={'scope': 'email'},
        base_url=app.config['OAUTH_BASE_URL'],
        request_token_url=app.config['OAUTH_REQUEST_TOKEN_URL'],
        access_token_url=app.config['OAUTH_ACCESS_TOKEN_URL'],
        authorize_url=app.config['OAUTH_AUTHORIZE_URL']
    )

    @app.remote_oauth.tokengetter
    def get_oauth_token():
        return session.get('remote_oauth')
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = 'true'
    server = WSGIServer((auction.host, auction.port, ), app)
    server.start()
    spawn(push_timestamps_events, app,)
    spawn(check_clients, app, )
    return server
