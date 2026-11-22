"""Практика 5: жизненный цикл очередей и три схемы маршрутизации."""
import argparse
import json
import os
import time
import pika

URL = os.getenv('AMQP_URL', 'amqp://social:social@localhost:5672/%2F')
MODES = {'fanout': ('#', True), 'direct': ('*', False), 'topic': ('-', True)}

def connect():
    return pika.BlockingConnection(pika.URLParameters(URL))

def queues():
    """Эксклюзивная очередь принадлежит соединению, а не двум программам."""
    with connect() as owner:
        ch = owner.channel()
        exclusive = ch.queue_declare(queue='', exclusive=True).method.queue
        ch.basic_publish('', exclusive, b'profile.created')
        method, _, body = ch.basic_get(exclusive, auto_ack=True)
        assert method and body == b'profile.created'
        print('EXCLUSIVE delivery:', body.decode())
        with connect() as other:
            try:
                other.channel().queue_declare(queue=exclusive, passive=True)
            except pika.exceptions.ChannelClosedByBroker as exc:
                assert exc.reply_code == 405
                print('EXCLUSIVE second connection rejected: 405')
        ch.queue_declare('social.durable', durable=True)
        ch.confirm_delivery()
        ch.basic_publish('', 'social.durable', b'post.created',
                         pika.BasicProperties(delivery_mode=2), mandatory=True)
        print('DURABLE persistent message published with confirm')
        auto = ch.queue_declare(queue='', auto_delete=True).method.queue
        tag = ch.basic_consume(auto, lambda *args: None, auto_ack=True)
        ch.basic_cancel(tag)
        print('AUTO_DELETE consumer registered and cancelled:', auto)
    with connect() as conn:
        for name, label in [(exclusive, 'EXCLUSIVE'), (auto, 'AUTO_DELETE')]:
            try:
                conn.channel().queue_declare(name, passive=True)
            except pika.exceptions.ChannelClosedByBroker as exc:
                assert exc.reply_code == 404
                print(label, 'queue removed: 404')

def topology(ch, mode):
    _, durable = MODES[mode]
    exchange = f'social.{mode}'
    ch.exchange_declare(exchange, exchange_type=mode, durable=durable)
    bindings = {'fanout': [('feed', ''), ('audit', '')],
                'direct': [('moderation', 'moderation'), ('notifications', 'notifications')],
                'topic': [('posts', 'content.post.*'), ('all_content', 'content.#')]}[mode]
    for suffix, key in bindings:
        queue = f'{exchange}.{suffix}'
        ch.queue_declare(queue, durable=durable)
        ch.queue_bind(queue, exchange, routing_key=key)
    return exchange, [f'{exchange}.{suffix}' for suffix, _ in bindings]

def publish(mode, key, workload):
    symbol, durable = MODES[mode]
    with connect() as conn:
        ch = conn.channel()
        exchange, _ = topology(ch, mode)
        ch.confirm_delivery()
        body = json.dumps({'event': 'post.created', 'workload': workload}, ensure_ascii=False)
        ch.basic_publish(exchange, key, body.encode(),
                         pika.BasicProperties(delivery_mode=2 if durable else 1, content_type='application/json'), mandatory=True)
        print('PUBLISHED', mode, key, 'sleep =', workload.count(symbol))

def worker(mode, queue, once=False, scale=1.0):
    symbol, _ = MODES[mode]
    with connect() as conn:
        ch = conn.channel()
        _, valid = topology(ch, mode)
        if queue not in valid:
            raise ValueError(f'Queue must be one of {valid}')
        ch.basic_qos(prefetch_count=1)
        def handle(channel, method, properties, body):
            try:
                event = json.loads(body)
                seconds = event['workload'].count(symbol) * scale
                time.sleep(seconds)
                print('PROCESSED', queue, event['event'], 'sleep =', seconds, flush=True)
                channel.basic_ack(method.delivery_tag)
            except (ValueError, KeyError, TypeError):
                channel.basic_nack(method.delivery_tag, requeue=False)
            if once:
                channel.stop_consuming()
        ch.basic_consume(queue, handle, auto_ack=False)
        ch.start_consuming()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['queues', 'publish', 'worker'])
    parser.add_argument('--mode', choices=list(MODES), default='fanout')
    parser.add_argument('--key', default='')
    parser.add_argument('--workload', default='###')
    parser.add_argument('--queue')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.action == 'queues':
        queues()
    elif args.action == 'publish':
        publish(args.mode, args.key, args.workload)
    else:
        worker(args.mode, args.queue, args.once)
