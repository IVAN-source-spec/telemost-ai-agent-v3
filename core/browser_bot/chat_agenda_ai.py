"""Bot-side client; the corporate model credentials remain on the control service."""
import json
import os
import urllib.request


def normalize_chat_agenda(raw, bot_id, session_id):
    if not raw.strip() or len(raw) > 20000:
        return {'status': 'invalid_ai_result'}
    url = os.getenv('CHAT_AGENDA_API_URL', '')
    token = os.getenv('CHAT_AGENDA_API_TOKEN', '')
    if not url or not token:
        return {'status': 'ai_unavailable'}
    payload = {'node_id': os.getenv('BOT_NODE_ID', ''), 'bot_id': bot_id,
               'session_id': session_id, 'raw_agenda': raw}
    try:
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
            'User-Agent': 'Telemost-Control-Monitor/1.0',
        })
        with urllib.request.urlopen(request, timeout=55) as response:
            result = json.loads(response.read(200000))
        if not isinstance(result, dict):
            raise ValueError('Invalid response')
        return result
    except Exception:
        return {'status': 'ai_unavailable'}
