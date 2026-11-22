"""python practice5/check_restart.py prepare|verify; между фазами restart RabbitMQ."""
import sys
from rabbit_demo import connect, topology, MODES, publish

def run(phase):
    if phase == 'prepare':
        with connect() as conn:
            ch = conn.channel()
            for mode in MODES:
                _, names = topology(ch, mode)
                for name in names:
                    ch.queue_purge(name)
        publish('fanout', '', '##')
        publish('topic', 'content.post.created', '--')
        publish('direct', 'moderation', '**')
        print('PREPARED persistent fanout/topic and transient direct')
        return
    with connect() as conn:
        ch = conn.channel()
        for mode in ['fanout', 'topic']:
            _, names = topology(ch, mode)
            for name in names:
                method, properties, body = ch.basic_get(name, auto_ack=False)
                assert method and properties.delivery_mode == 2
                ch.basic_ack(method.delivery_tag)
                print('PASS after restart:', name, body.decode())
        # До объявления топологии отсутствие transient-очереди проверяется passive.
        try:
            conn.channel().queue_declare('social.direct.moderation', passive=True)
            raise AssertionError('Transient queue unexpectedly survived')
        except Exception as exc:
            if getattr(exc, 'reply_code', None) != 404:
                raise
        print('PASS transient direct queue absent after restart')

if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in ['prepare', 'verify']:
        raise SystemExit('Usage: check_restart.py prepare|verify')
    run(sys.argv[1])
