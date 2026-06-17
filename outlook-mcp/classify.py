import json, re, fnmatch, sys, io
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

path = sys.argv[1]

with open(path, 'r', encoding='utf-8') as f:
    data = json.load(f)

text = data[0]['text']
entries = re.split(r'\n\n(?=\d+\.\s)', text)

emails = []
for entry in entries:
    m = re.search(r'From:\s+.*?\((.*?)\)', entry)
    s = re.search(r'Subject:\s+(.*)', entry)
    i = re.search(r'ID:\s+(\S+)', entry)
    d = re.search(r'(\d{2}/\d{2}/\d{4})', entry)
    if m and s and i:
        emails.append({
            'from': m.group(1).strip().lower(),
            'subject': s.group(1).strip(),
            'id': i.group(1).strip(),
            'date': d.group(1).strip() if d else ''
        })

booking_keywords = ['booking confirmation', 'reservation confirmed', 'order confirmation',
    'your booking', 'e-ticket', 'itinerary', 'check-in details', 'your table', 'test drive']

security_patterns = ['*security alert*', '*account*compromised*', '*unauthorized*sign*in*',
    '*unusual*sign*in*', '*account*breach*', '*password*changed*']

keep_senders = [
    '*@elephant.co.uk', '*@accounts.google.com', '*@accountprotection.microsoft.com',
    '*@leasingoptions.co.uk', '*@global.e-vanguard.co.uk', '*@tax.service.gov.uk',
    '*@messaging.firstdirect.com', '*@email.firstdirect.com', '*@*mandsbank.com',
    '*@octopus.energy', '*@welcome.americanexpress.com', '*@clearscore.com',
    '*@virginmediao2.co.uk', '*@admiral.co.uk', '*@mail.anthropic.com',
    '*@petdrugsonline.co.uk', '*@miele-support.co.uk', '*@delivery.miele.com',
    '*@communications.paypal.com', '*@dpd.co.uk', '*@dxdelivery.com', '*@yodel.co.uk',
    '*@nothing.tech', '*@e.nothing.tech', '*@github.com', '*@mail.cursor.com',
    '*@cursor.sh', '*@google.com', '*@email.openai.com', '*@account.netflix.com',
    '*@stripe.com', '*@patreon.com', '*@microsoft.com', '*@royalalberthall.com',
    '*@sims-communications.co.uk', '*@audible.co.uk', '*@amazonaws.com',
    '*@leasing.com', '*@id.worx.com', '*@gmail.com', '*@hotmail.com', '*@outlook.com',
    '*@service.bestinvest.co.uk', '*@vanguardinvestor.co.uk', '*@virginmedia.co.uk',
    '*@*virginmediao2.co.uk', '*@email.idmobile.co.uk', '*@bromley.gov.uk',
    '*@byd.com', '*@wearegifted.co.uk', '*@bp.com', '*@shell.com', '*@myworkday.com',
    '*@esignature.shell.com', '*@saintolaves.net', '*@ipo.gov.uk', '*@advice.hmrc.gov.uk',
    '*@email.ajbell.co.uk', '*@notifications.virginmedia.co.uk', '*@screwfix.com',
    '*@thegreenhouseshop.co.uk', '*@lexautolease.co.uk'
]

archive_senders = [
    '*@southlondonmakerspace.org', '*@bambulab.com', '*@hello.stackoverflow.email',
    '*@microsoftstore.microsoft.com', '*@email.theguardian.com', '*@gastronovi.de',
    '*@quandoo.com', '*@email.thefork.co.uk', '*@goodfood.com', '*@printables.com',
    '*@steampowered.com'
]

trash_senders = [
    '*@chordify.net', '*@email.politico.com', '*@mc.politico.eu', '*@politico.eu',
    '*@spectator.co.uk', '*@makerworld.com', '*@update.just-eat.co.uk',
    '*@mail.groupgreeting.com', '*@notify.experian.co.uk', '*@servicing.experian.co.uk',
    '*@email.moneysavingexpert.com', '*@email3.moneysavingexpert.com', '*@airbnb.com',
    '*@nannybag.com', '*@bvg.de', '*@booking.com', '*@sg.booking.com',
    '*@t.deliveroo.com', '*@feedback.deliveroo.com', '*@community.pixabay.com',
    '*@trustpilotmail.com', '*@tripadvisor.com', '*@t1.tripadvisor.com',
    '*@mg.zenchefrestaurants.com', '*@groupgreeting.com', '*@uk.enews.bp.email',
    '*@linkedin.com', '*@em.linkedin.com', '*@dominos.co.uk',
    '*@email.dominosmarketing.co.uk', '*@email.dominos.co.uk', '*@mail.sockshop.co.uk',
    '*@nutracheck.co.uk', '*@*easyjet.com', '*@spotify.com', '*@facebookmail.com',
    '*@amazon.co.uk', '*@paypal.co.uk', '*@wjking.co.uk', '*@growatt-service.com',
    '*@app.hiive.com', '*@web.toast.co.uk', '*@*lovehoney*', '*@dhlecommerce.co.uk',
    '*@sfmail.gatwickairport.com', '*@*leaseloco.com', '*@crowdexpert.org',
    '*@bigeasy.co.uk', '*@avis-comms.international', '*@ukpoolstore.co.uk',
    '*@cititec.com', '*@mailing.cgtrader.com', '*@e.sigmaaldrich.com',
    '*@restore.halfords.com', '*@emails.virginatlantic.com',
    '*@servicemail.photobox.co.uk', '*@mail.aircanada.com', '*@germainhotels.com',
    '*@autodeskcommunications.com', '*@reviews.co.uk',
    '*@mail.newsletter.instructables.com', '*@kickstartrack.com',
    '*@express.medallia.com', '*@eml.johnlewis.com', '*@nakedwines.com',
    '*@e.teksystems.com', '*@geraldgiles.co.uk', '*@marketing.photobox.co.uk',
    '*@email.thehawksmoor.com', '*@mg.co.uk', '*@shipping.amazon.co.uk',
    '*@smartwatch-straps.co.uk', '*@info.cgtrader.com', '*@bigumigu.com',
    '*@reply.lights.co.uk', '*@updates.ubisoft.com', '*@service.thehawksmoor.com',
    '*@service.visionexpress.com', '*@qualtrics-survey.com', '*@inguest.com',
    '*@my.zooplus.co.uk', '*@indianessence.co.uk', '*@news.bambulab.com',
    '*@service.virginatlantic.com'
]

trash_subject_patterns = ['*out of*storage*', '*storage*full*']

def match_sender(email_addr, patterns):
    for p in patterns:
        if fnmatch.fnmatch(email_addr, p):
            return True
    return False

def match_subject(subj, patterns):
    subj_lower = subj.lower()
    for p in patterns:
        if fnmatch.fnmatch(subj_lower, p):
            return True
    return False

trash_ids = []
archive_ids = []
keep_ids = []
unmatched = []
trash_details = []
archive_details = []

for e in emails:
    sender = e['from']
    subj_lower = e['subject'].lower()

    # 1. Booking detection (all past bookings -> trash)
    is_booking = any(kw in subj_lower for kw in booking_keywords)
    if is_booking:
        trash_ids.append(e['id'])
        trash_details.append(f"  [BOOKING] {e['date']} | {e['from'][:40]} | {e['subject'][:55]}")
        continue

    # 2. Security subjects -> keep
    if match_subject(e['subject'], security_patterns):
        keep_ids.append(e['id'])
        continue

    # 3. Keep senders
    if match_sender(sender, keep_senders):
        keep_ids.append(e['id'])
        continue

    # 4. Archive senders
    if match_sender(sender, archive_senders):
        archive_ids.append(e['id'])
        archive_details.append(f"  {e['date']} | {e['from'][:40]} | {e['subject'][:55]}")
        continue

    # 5. Trash senders
    if match_sender(sender, trash_senders):
        trash_ids.append(e['id'])
        trash_details.append(f"  {e['date']} | {e['from'][:40]} | {e['subject'][:55]}")
        continue

    # 6. Trash subjects
    if match_subject(e['subject'], trash_subject_patterns):
        trash_ids.append(e['id'])
        trash_details.append(f"  [SUBJ] {e['date']} | {e['from'][:40]} | {e['subject'][:55]}")
        continue

    # Unmatched
    unmatched.append(e)

print(f'Total: {len(emails)}')
print(f'Trash: {len(trash_ids)}')
print(f'Archive (em-notifications): {len(archive_ids)}')
print(f'Keep: {len(keep_ids)}')
print(f'Unmatched: {len(unmatched)}')

print('\n--- Unmatched emails ---')
unmatched_domains = Counter()
for e in unmatched:
    domain = e['from'].split('@')[-1] if '@' in e['from'] else e['from']
    unmatched_domains[domain] += 1
    print(f"  {e['date']} | {e['from'][:45]} | {e['subject'][:55]}")

print('\n--- Unmatched sender domains ---')
for domain, count in unmatched_domains.most_common():
    print(f'  {domain}: {count}')

# Save IDs
outdir = r"C:\Users\gethi\source\email-manager\outlook-mcp"
with open(outdir + r'\batch_trash.json', 'w') as f:
    json.dump(trash_ids, f)
with open(outdir + r'\batch_archive.json', 'w') as f:
    json.dump(archive_ids, f)

print(f'\nSaved {len(trash_ids)} trash IDs and {len(archive_ids)} archive IDs')
