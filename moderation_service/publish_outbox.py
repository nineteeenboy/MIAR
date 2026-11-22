"""Повторная отправка подтвержденных решений из outbox."""
import argparse
import time
import pika
from moderation_service.app import dispatch_pending

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    while True:
        try:
            print('DELIVERED', dispatch_pending(), flush=True)
        except pika.exceptions.AMQPError as error:
            print('RETRY', type(error).__name__, flush=True)
            if args.once:
                raise
        if args.once:
            break
        time.sleep(5)
