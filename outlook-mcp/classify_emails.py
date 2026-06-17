import json
import sys
import re
import fnmatch

# KEEP patterns
KEEP_PATTERNS = [
    "*@elephant.co.uk", "*@accounts.google.com", "*@accountprotection.microsoft.com",
    "*@*firstdirect.com", "*@*mandsbank.com", "*@octopus.energy", "*@*americanexpress.com",
    "*@clearscore.com", "*@virginmediao2.co.uk", "*@admiral.co.uk", "*@petdrugsonline.co.uk",
    "*@dpd.co.uk", "*@yodel.co.uk", "*@github.com", "*@google.com", "*@account.netflix.com",
    "*@stripe.com", "*@microsoft.com", "*@sims-communications.co.uk", "*@audible.co.uk",
    "*@amazonaws.com", "*@gmail.com", "*@hotmail.com", "*@outlook.com",
    "*@*bestinvest.co.uk", "*@vanguardinvestor.co.uk", "*@virginmedia.co.uk",
    "*@*virginmediao2.co.uk", "*@bromley.gov.uk", "*@bp.com", "*@saintolaves.net",
    "*@ipo.gov.uk", "*@advice.hmrc.gov.uk", "*@screwfix.com",
    "*@notifications.virginmedia.co.uk", "*@tesla.com", "*@royalalberthall.com",
    "*@tax.service.gov.uk",
]

TRASH_PATTERNS = [
    "*@linkedin.com", "*@em.linkedin.com", "*@update.just-eat.co.uk", "*@amazon.co.uk",
    "*@paypal.co.uk", "*@spotify.com", "*@facebookmail.com", "*@*easyjet.com",
    "*@nutracheck.co.uk", "*@dominos.co.uk", "*@mail.sockshop.co.uk",
    "*@sfmail.gatwickairport.com", "*@web.toast.co.uk", "*@*lovehoney*",
    "*@booking.com", "*@trustpilotmail.com", "*@tripadvisor.com",
    "*@eml.johnlewis.com", "*@nakedwines.com", "*@newsteadwood.co.uk",
    "*@emails.telegraph.co.uk", "*@wisepay.co.uk", "*@shipping.amazon.co.uk",
    "*@bigeasy.co.uk", "*@wjking.co.uk", "*@marketing.photobox.co.uk",
    "*@qualtrics-survey.com", "*@positecgroup.com", "*@growingsmiles.co.uk",
    "*@bigumigu.com", "*@service.visionexpress.com", "*@geraldgiles.co.uk",
    "*@emails.virginatlantic.com", "*@reviews.co.uk", "*@cititec.com",
    "*@germainhotels.com", "*@reply.lights.co.uk",
]

TRASH_SUBJECT_PATTERNS = [
    "*out of*storage*", "*storage*full*",
]

def extract_email_addr(from_field):
    """Extract email address from 'Name (email@domain)' format."""
    m = re.search(r'\(([^)]+)\)', from_field)
    if m:
        return m.group(1).lower()
    return from_field.lower().strip()

def parse_emails(text):
    """Parse the search results text into email dicts."""
    emails = []
    # Pattern: number. [UNREAD] dd/mm/yyyy, hh:mm:ss - From: Name (email)\nSubject: ...\nID: ...
    pattern = r'(\d+)\.\s+(?:\[UNREAD\]\s+)?(\d{2}/\d{2}/\d{4}),\s+(\d{2}:\d{2}:\d{2})\s+-\s+From:\s+(.+?)\nSubject:\s*(.*?)\nID:\s*(\S+)'
    for m in re.finditer(pattern, text):
        emails.append({
            'num': int(m.group(1)),
            'date': m.group(2),
            'time': m.group(3),
            'from': m.group(4).strip(),
            'subject': m.group(5).strip(),
            'id': m.group(6).strip(),
        })
    return emails

def classify(email):
    addr = extract_email_addr(email['from'])
    subject = email.get('subject', '').lower()

    # Check KEEP patterns
    for p in KEEP_PATTERNS:
        if fnmatch.fnmatch(addr, p.lower()):
            return 'KEEP'

    # Check TRASH patterns
    for p in TRASH_PATTERNS:
        if fnmatch.fnmatch(addr, p.lower()):
            return 'TRASH'

    # Check TRASH subject patterns
    for p in TRASH_SUBJECT_PATTERNS:
        if fnmatch.fnmatch(subject, p.lower()):
            return 'TRASH'

    # Booking subjects - past bookings, trash
    if 'booking' in subject.lower():
        return 'TRASH'

    return 'UNMATCHED'

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    text = data[0]['text']
    emails = parse_emails(text)
    return emails

if __name__ == '__main__':
    files = sys.argv[1:]
    all_emails = []
    seen_ids = set()

    for filepath in files:
        emails = process_file(filepath)
        for e in emails:
            if e['id'] not in seen_ids:
                seen_ids.add(e['id'])
                all_emails.append(e)

    keep_ids = []
    trash_ids = []
    unmatched = []

    for e in all_emails:
        c = classify(e)
        if c == 'KEEP':
            keep_ids.append(e['id'])
        elif c == 'TRASH':
            trash_ids.append(e)
        else:
            unmatched.append(e)

    # Output summary
    print(f"Total unique emails: {len(all_emails)}")
    print(f"KEEP: {len(keep_ids)}")
    print(f"TRASH: {len(trash_ids)}")
    print(f"UNMATCHED: {len(unmatched)}")

    # Output trash IDs for batch processing
    print("\n=== TRASH IDS ===")
    for i in range(0, len(trash_ids), 50):
        batch = trash_ids[i:i+50]
        ids = ",".join(e['id'] for e in batch)
        print(f"BATCH {i//50 + 1}: {ids}")

    # Output unmatched domains
    print("\n=== UNMATCHED DOMAINS ===")
    domain_counts = {}
    for e in unmatched:
        addr = extract_email_addr(e['from'])
        domain = addr.split('@')[-1] if '@' in addr else addr
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

    for domain, count in sorted(domain_counts.items(), key=lambda x: -x[1]):
        print(f"  {domain}: {count}")

    # Also print unmatched details
    print("\n=== UNMATCHED DETAILS ===")
    for e in unmatched:
        print(f"  {e['date']} | {e['from']} | {e['subject']}")
