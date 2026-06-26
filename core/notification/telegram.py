def build_incident_message(error_code: str, bot_id: str, meeting_id: str) -> str:
    return f"Incident detected\nerror={error_code}\nbot_id={bot_id}\nmeeting_id={meeting_id}"


def build_recovery_message(event_code: str, bot_id: str, meeting_id: str) -> str:
    return f"Recovery event\nevent={event_code}\nbot_id={bot_id}\nmeeting_id={meeting_id}"


def build_qr_delivery_message(qr_url: str, ttl_seconds: int) -> str:
    return f"QR authorization\nurl={qr_url}\nttl_seconds={ttl_seconds}"