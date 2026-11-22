 """Сквозной сценарий, включающий все шесть запущенных микросервисов."""
from sqlalchemy import text
from conftest import URLS,headers,MODERATOR

def test_six_services_social_network_flow(http,person,post,community,dialog,make_report,sql):
    # Fixtures create profiles through Users, a post through Content,
    # a community through Community and a dialog through Messenger.
    second={'id':next(value for value in dialog['participant_ids'] if value!=person['id'])}
    joined=http.put(URLS['community']+f"/api/v1/communities/{community['id']}/members/me",headers=headers(second))
    assert joined.status_code==204
    comment=http.post(URLS['interaction']+f"/api/v1/posts/{post['id']}/comments",
                      headers=headers(second),json={'text':'Обсудим публикацию в сообществе'})
    assert comment.status_code==201
    message=http.post(URLS['messenger']+f"/api/v1/dialogs/{dialog['id']}/messages",
                      headers=headers(person),json={'text':'Комментарий к публикации отправлен'})
    assert message.status_code==201
    read=http.patch(URLS['messenger']+f"/api/v1/dialogs/{dialog['id']}/read",
                    headers=headers(second),json={'last_read_message_id':message.json()['id']})
    assert read.status_code==200
    report=make_report('comment',comment.json()['id'])
    decision=http.patch(URLS['moderation']+f"/api/v1/reports/{report['id']}",headers=MODERATOR,
                        json={'status':'rejected','decision':'Комментарий соответствует правилам'})
    assert decision.status_code==200
    own_report=http.get(URLS['moderation']+f"/api/v1/reports/{report['id']}",headers=headers(person))
    assert own_report.json()['status']=='rejected'
    with sql['moderation'].connect() as db:
        assert db.execute(text('SELECT sent FROM outbox WHERE report_id=:id'),{'id':report['id']}).scalar_one() is True
