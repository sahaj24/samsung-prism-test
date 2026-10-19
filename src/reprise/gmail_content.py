"""Bounded MIME text extraction. Attachments and remote HTML resources are not read."""
import base64
import binascii
import re
from html.parser import HTMLParser


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden += 1
        if tag in {'br', 'p', 'div', 'tr', 'td', 'li'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {'p', 'div', 'tr', 'td', 'li'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def message_text(payload, depth=0):
    if depth > 12 or payload.get('filename'):
        return ''
    kind = payload.get('mimeType', '')
    parts = payload.get('parts', [])
    if parts:
        texts = [(part.get('mimeType'), message_text(part, depth + 1)) for part in parts[:40]]
        if kind == 'multipart/alternative':
            plain = [text for mime, text in texts if mime == 'text/plain' and text]
            return (plain[0] if plain else next((text for _, text in texts if text), ''))[:12000]
        return '\n'.join(text for _, text in texts if text)[:12000]
    if kind not in {'text/plain', 'text/html'}:
        return ''
    encoded = payload.get('body', {}).get('data', '')
    if not encoded:
        return ''
    # Cap decoded input before parsing. No attachment fetches.
    encoded = encoded[:65536]
    try:
        text = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)).decode('utf-8', errors='replace')
    except (ValueError, binascii.Error):
        return ''
    if kind == 'text/html':
        parser = PlainHTML()
        parser.feed(text)
        text = ''.join(parser.parts)
    return text[:12000]


def source_details(message):
    headers = {h['name'].casefold(): h.get('value', '') for h in message.get('payload', {}).get('headers', [])}
    return {'message_id': message['id'], 'subject': headers.get('subject', '')[:300],
            'from': headers.get('from', '')[:300], 'date': headers.get('date', '')[:150]}


def booking_fields(text):
    """Extract explicit labeled lines verbatim; conflicting values remain unresolved."""
    labels = {
        "pnr": r"PNR(?: number)?|booking reference",
        "flight_number": r"flight(?: number| no\.?)?",
        "departure_date": r"departure date|date of departure",
        "departure_time": r"departure time|time of departure",
    }
    fields = {}
    for key, label in labels.items():
        matches = list(re.finditer(r"^(?:" + label + r")\s*[:：]\s*([^\r\n]{1,100})$", text, re.I | re.M))
        values = {m.group(1).strip() for m in matches}
        if len(values) == 1 and next(iter(values)):
            fields[key] = {"value": matches[0].group(1).strip(), "evidence": matches[0].group(0)}
    return fields
