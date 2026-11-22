"""Отправитель событий социальной сети в очереди трех типов."""
import argparse
import json
from uuid import uuid4
import pika
from rabbit_demo import connect

NAMES = {'exclusive': 'social.exclusive',
         'durable': 'social.durable',
         'auto': 'social.auto_delete'}

def send(kind):
    with connect() as connection:
        channel = connection.channel()
        if kind == 'durable':
            channel.queue_declare(NAMES[kind], durable=True)
        # Временные очереди создает получатель своим соединением.
        channel.confirm_delivery()
        event = {'event_id': str(uuid4()), 'event': 'post.created',
                 'author_id': 'b2a5054b-34d2-4878-ac2e-c407b8a26ba1',
                 'text': 'Новая публикация в социальной сети'}
        channel.basic_publish('', NAMES[kind],
            json.dumps(event, ensure_ascii=False).encode(),
            pika.BasicProperties(content_type='application/json',
                                 delivery_mode=2 if kind == 'durable' else 1),
            mandatory=True)
        print('SENT', NAMES[kind], event['event'], flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=NAMES)
    send(parser.parse_args().kind)
