from flask_redis import Redis
from flask import Flask, render_template, request, abort, Response

import couchdb
from datetime import datetime
from pytz import timezone as tz
from paste.proxy import make_proxy
from urlparse import urljoin
from gevent import monkey, sleep
from wsgiproxy import HostProxy

monkey.patch_all()

auctions_server = Flask(
    __name__,
    static_url_path='',
    template_folder='static'
)


@auctions_server.before_request
def before_request():
    auctions_server.logger.debug('Start {0.method}: {0.url}'.format(request))


@auctions_server.after_request
def after_request(response):
    auctions_server.logger.debug(
        'End {1.status_code} : {0.method} : {0.url} '.format(request, response)
    )
    return response


@auctions_server.route('/tenders/<auction_doc_id>')
def auction_url(auction_doc_id):
    return render_template(
        'index.html',
        db_url=auctions_server.config.get('EXT_COUCH_DB'),
        auction_doc_id=auction_doc_id
    )


@auctions_server.route('/')
def archive_tenders_list_index():
    map_fun = '''function(doc) {
        var current_time = new Date();
        var end = new Date(doc.endDate);
        if (current_time<end){
            emit(doc.stages[0].start, doc);
        }
    }'''
    return render_template(
        'list.html',
        documents=[auction.value for auction in auctions_server.db.query(map_fun)]
    )


@auctions_server.route('/archive')
def auction_list_index():
    map_fun = '''function(doc) {
        var current_time = new Date();
        var end = new Date(doc.endDate);
        if ((doc.endDate)&&(current_time>end)){
            emit(doc.stages[0].start, doc);
        }
    }'''
    return render_template(
        'list.html',
        documents=[auction.value for auction in auctions_server.db.query(map_fun)]
    )


@auctions_server.route('/tenders/<auction_doc_id>/<path:path>')
def auctions_proxy(auction_doc_id, path):
    print "---------------- auth", request.authorization
    proxy_path = auctions_server.redis.get(auction_doc_id)
    auctions_server.logger.debug('Proxy path: {}'.format(proxy_path))
    if proxy_path:
        request.environ['PATH_INFO'] = '/' + path
        return HostProxy(proxy_path, client='requests', chunk_size=1)
    else:
        return abort(404)


@auctions_server.route('/get_current_server_time')
def auctions_server_current_server_time():
    return datetime.now(auctions_server.config['TIMEZONE']).isoformat()


def couch_server_proxy(path):
    return make_proxy(
        {}, auctions_server.config['INT_COUCH_URL'], allowed_request_methods="",
        suppress_http_headers="")


def make_auctions_app(global_conf,
                      redis_url='redis://localhost:7777/0',
                      external_couch_url='http://localhost:5000/auction',
                      internal_couch_url='http://localhost:9000/',
                      auctions_db='auctions',
                      timezone='Europe/Kiev'):
    """
    [app:main]
    use = egg:openprocurement.auction#auctions_server
    redis_url = redis://:passwod@localhost:1111/0
    external_couch_url = http://localhost:1111/auction
    internal_couch_url = http://localhost:9011/
    auctions_db = auction
    timezone = Europe/Kiev
    """

    auctions_server.config['REDIS_URL'] = redis_url
    auctions_server.config['EXT_COUCH_DB'] = urljoin(
        external_couch_url,
        auctions_db
    )
    auctions_server.add_url_rule(
        '/' + auctions_db + '/<path:path>',
        'couch_server_proxy',
        couch_server_proxy,
        methods=['GET'])
    auctions_server.add_url_rule(
        '/' + auctions_db + '/',
        'couch_server_proxy',
        couch_server_proxy,
        methods=['GET'], defaults={'path': ''})
    auctions_server.config['INT_COUCH_URL'] = internal_couch_url
    auctions_server.config['COUCH_DB'] = auctions_db
    auctions_server.config['TIMEZONE'] = tz(timezone)
    auctions_server.redis = Redis(auctions_server)
    auctions_server.db = couchdb.client.Database(
        urljoin(auctions_server.config.get('INT_COUCH_URL'),
                auctions_server.config['COUCH_DB'])
    )
    return auctions_server
