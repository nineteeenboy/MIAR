"""Получатель событий; свойства очереди задаются выбранным типом."""
import argparse
import json
from rabbit_demo import connect
from queue_sender import NAMES

def receive(kind):
    with connect() as connection:
        channel = connection.channel()
        channel.queue_declare(NAMES[kind],
            durable=kind == 'durable', exclusive=kind == 'exclusive',
            auto_delete=kind == 'auto')
        channel.basic_qos(prefetch_count=1)
        def handle(ch, method, properties, body):
            event = json.loads(body)
            print('RECEIVED', NAMES[kind], event['event'], flush=True)
            ch.basic_ack(method.delivery_tag)
        channel.basic_consume(NAMES[kind], handle, auto_ack=False)
        print('WAITING', NAMES[kind], flush=True)
        try:
            channel.start_consuming()
        except KeyboardInterrupt:
            channel.stop_consuming()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=NAMES)
    receive(parser.parse_args().kind)
