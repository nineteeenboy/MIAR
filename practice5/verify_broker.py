"""Автоматическая проверка маршрутизации на реальном RabbitMQ."""
import json
import pika
from rabbit_demo import connect, topology, MODES, publish, worker, queues

def verify():
    queues()
    for mode, (symbol, durable) in MODES.items():
        with connect() as conn:
            ch = conn.channel()
            _, names = topology(ch, mode)
            for name in names:
                ch.queue_purge(name)
        key = {'fanout': '', 'direct': 'moderation', 'topic': 'content.post.created'}[mode]
        publish(mode, key, symbol * 2)
        expected = names if mode != 'direct' else names[:1]
        for name in expected:
            worker(mode, name, once=True, scale=0.01)
        with connect() as conn:
            ch = conn.channel()
            for name in names:
                assert ch.queue_declare(name, passive=True).method.message_count == 0
        print('PASS routing:', mode, 'durable:', durable)
    print('ALL BROKER CHECKS PASSED')

if __name__ == '__main__':
    verify()
