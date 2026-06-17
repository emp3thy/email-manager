const fs = require('fs');

// Load all emails from files
const emailRegex = /(\d+)\.\s+(?:\[UNREAD\]\s+)?(\d{2}\/\d{2}\/\d{4}),\s+[\d:]+\s+-\s+From:\s+(.+?)\s+\(([^)]+)\)\nSubject:\s+(.*?)\nID:\s+(\S+)/g;

function parseFile(filepath) {
  const raw = fs.readFileSync(filepath, 'utf8');
  let text;
  try {
    const data = JSON.parse(raw);
    text = Array.isArray(data) ? data[0].text : data.text;
  } catch(e) {
    text = raw;
  }
  const emails = [];
  let match;
  emailRegex.lastIndex = 0;
  while ((match = emailRegex.exec(text)) !== null) {
    emails.push({ date: match[2], fromEmail: match[4].toLowerCase(), subject: match[5], id: match[6] });
  }
  return emails;
}

const base = 'C:/Users/gethi/.claude/projects/C--Users-gethi-source-email-manager/c871d9cd-1771-427b-8bc4-273a38576d1e/tool-results/';
const files = [
  base + 'mcp-outlook-manager-search-emails-1773577923908.txt',
  base + 'mcp-outlook-manager-search-emails-1773577945788.txt',
  base + 'mcp-outlook-manager-search-emails-1773577977130.txt',
  base + 'mcp-outlook-manager-search-emails-1773577949919.txt',
];

const allEmails = new Map();
for (const f of files) {
  const emails = parseFile(f);
  for (const e of emails) {
    allEmails.set(e.id, e);
  }
}
console.log('From files:', allEmails.size);

// Filter to Jul 1 - Oct 4 range
const filtered = [];
for (const e of allEmails.values()) {
  const parts = e.date.split('/');
  const d = new Date(parseInt(parts[2]), parseInt(parts[1])-1, parseInt(parts[0]));
  if (d >= new Date(2024, 6, 1) && d <= new Date(2024, 9, 4)) {
    filtered.push(e);
  }
}
console.log('In Jul 1 - Oct 4 range:', filtered.length);

// Glob matching
function globMatch(pattern, text) {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  const regex = new RegExp('^' + escaped.replace(/\*/g, '.*').replace(/\?/g, '.') + '$', 'i');
  return regex.test(text);
}

// Booking detection subjects
const bookingPatterns = ['booking confirmation', 'reservation confirmed', 'order confirmation', 'your booking', 'e-ticket', 'itinerary', 'check-in details', 'your table', 'test drive'];

// Security subjects
const securityPatterns = ['*security alert*', '*account*compromised*', '*unauthorized*sign*in*', '*unusual*sign*in*'];

// Keep senders
const keepSenders = [
  '*@elephant.co.uk', '*@accounts.google.com', '*@accountprotection.microsoft.com',
  '*@global.e-vanguard.co.uk', '*@tax.service.gov.uk', '*@messaging.firstdirect.com',
  '*@email.firstdirect.com', '*@*mandsbank.com', '*@octopus.energy',
  '*@welcome.americanexpress.com', '*@*americanexpress.com', '*@clearscore.com',
  '*@virginmediao2.co.uk', '*@admiral.co.uk', '*@petdrugsonline.co.uk',
  '*@miele-support.co.uk', '*@delivery.miele.com', '*@dpd.co.uk', '*@dxdelivery.com',
  '*@yodel.co.uk', '*@nothing.tech', '*@e.nothing.tech', '*@github.com', '*@google.com',
  '*@account.netflix.com', '*@stripe.com', '*@patreon.com', '*@microsoft.com',
  '*@royalalberthall.com', '*@sims-communications.co.uk', '*@audible.co.uk',
  '*@amazonaws.com', '*@gmail.com', '*@hotmail.com', '*@outlook.com',
  '*@service.bestinvest.co.uk', '*@vanguardinvestor.co.uk', '*@virginmedia.co.uk',
  '*@*virginmediao2.co.uk', '*@email.idmobile.co.uk', '*@bromley.gov.uk',
  '*@bp.com', '*@shell.com', '*@myworkday.com', '*@esignature.shell.com',
  '*@saintolaves.net', '*@ipo.gov.uk', '*@advice.hmrc.gov.uk', '*@email.ajbell.co.uk',
  '*@screwfix.com', '*@lexautolease.co.uk', '*@notifications.virginmedia.co.uk',
  '*@tesla.com', '*@thegreenhouseshop.co.uk', '*@wearegifted.co.uk'
];

// Archive senders (em-notifications)
const archiveSenders = [
  '*@southlondonmakerspace.org', '*@bambulab.com', '*@steampowered.com', '*@printables.com'
];

// Trash senders
const trashSenders = [
  '*@linkedin.com', '*@em.linkedin.com', '*@update.just-eat.co.uk', '*@amazon.co.uk',
  '*@paypal.co.uk', '*@spotify.com', '*@facebookmail.com', '*@*easyjet.com',
  '*@nutracheck.co.uk', '*@dominos.co.uk', '*@mail.sockshop.co.uk', '*@dhlecommerce.co.uk',
  '*@sfmail.gatwickairport.com', '*@growatt-service.com', '*@app.hiive.com',
  '*@web.toast.co.uk', '*@*lovehoney*', '*@booking.com', '*@sg.booking.com',
  '*@trustpilotmail.com', '*@tripadvisor.com', '*@kickstartrack.com',
  '*@express.medallia.com', '*@eml.johnlewis.com', '*@nakedwines.com',
  '*@smartwatch-straps.co.uk', '*@newsteadwood.co.uk', '*@emails.telegraph.co.uk',
  '*@wisepay.co.uk', '*@shipping.amazon.co.uk', '*@crowdexpert.org', '*@bigeasy.co.uk',
  '*@wjking.co.uk', '*@mg.co.uk', '*@restore.halfords.com', '*@marketing.photobox.co.uk',
  '*@servicemail.photobox.co.uk', '*@email.thehawksmoor.com', '*@service.thehawksmoor.com',
  '*@qualtrics-survey.com', '*@inguest.com', '*@positecgroup.com', '*@growingsmiles.co.uk',
  '*@news.eufylife.com', '*@mayfair.thebeaumont.com', '*@newsletter.hottershoes.com',
  '*@bigumigu.com', '*@service.visionexpress.com', '*@indianessence.co.uk',
  '*@my.zooplus.co.uk', '*@geraldgiles.co.uk', '*@info.cgtrader.com',
  '*@mailing.cgtrader.com', '*@e.sigmaaldrich.com', '*@emails.virginatlantic.com',
  '*@service.virginatlantic.com', '*@mail.aircanada.com', '*@autodeskcommunications.com',
  '*@reviews.co.uk', '*@e.teksystems.com', '*@avis-comms.international',
  '*@ukpoolstore.co.uk', '*@cititec.com', '*@news.bambulab.com'
];

// Trash subjects
const trashSubjects = ['*out of*storage*', '*storage*full*'];

// Classify
const results = { trash: [], keep: [], archive: [], unmatched: [] };
const unmatchedDomains = {};

for (const email of filtered) {
  let classified = false;
  const subjectLower = email.subject.toLowerCase();

  // 1. Booking detection (FIRST)
  for (const bp of bookingPatterns) {
    if (subjectLower.includes(bp)) {
      results.trash.push(email);
      classified = true;
      break;
    }
  }
  if (classified) continue;

  // 2. Security subjects -> KEEP
  for (const sp of securityPatterns) {
    if (globMatch(sp, subjectLower)) {
      results.keep.push(email);
      classified = true;
      break;
    }
  }
  if (classified) continue;

  // 3. Keep senders
  for (const ks of keepSenders) {
    if (globMatch(ks, email.fromEmail)) {
      results.keep.push(email);
      classified = true;
      break;
    }
  }
  if (classified) continue;

  // 4. Archive senders
  for (const as of archiveSenders) {
    if (globMatch(as, email.fromEmail)) {
      results.archive.push(email);
      classified = true;
      break;
    }
  }
  if (classified) continue;

  // 5. Trash senders
  for (const ts of trashSenders) {
    if (globMatch(ts, email.fromEmail)) {
      results.trash.push(email);
      classified = true;
      break;
    }
  }
  if (classified) continue;

  // 6. Trash subjects
  for (const ts of trashSubjects) {
    if (globMatch(ts, subjectLower)) {
      results.trash.push(email);
      classified = true;
      break;
    }
  }
  if (classified) continue;

  // Unmatched
  results.unmatched.push(email);
  const domain = email.fromEmail.split('@')[1] || 'unknown';
  unmatchedDomains[domain] = (unmatchedDomains[domain] || 0) + 1;
}

console.log('\n=== CLASSIFICATION RESULTS ===');
console.log('Total processed:', filtered.length);
console.log('Trash:', results.trash.length);
console.log('Keep:', results.keep.length);
console.log('Archive (em-notifications):', results.archive.length);
console.log('Unmatched:', results.unmatched.length);

console.log('\n=== UNMATCHED DOMAINS ===');
const sortedDomains = Object.entries(unmatchedDomains).sort((a,b) => b[1] - a[1]);
for (const [domain, count] of sortedDomains) {
  console.log(`  ${domain}: ${count}`);
}

// Save IDs for batch operations
const path = require('path');
const dir = __dirname;

// Write trash batches of 50
const trashIds = results.trash.map(e => e.id);
for (let i = 0; i < Math.ceil(trashIds.length / 50); i++) {
  const batch = trashIds.slice(i * 50, (i + 1) * 50);
  fs.writeFileSync(path.join(dir, 'trash_batch_' + i + '.txt'), batch.join(','));
}
console.log('Trash batches written:', Math.ceil(trashIds.length / 50));

// Write archive batch
const archiveIds = results.archive.map(e => e.id);
fs.writeFileSync(path.join(dir, 'archive_batch.txt'), archiveIds.join(','));

// Write unmatched for reference
fs.writeFileSync(path.join(dir, 'unmatched.json'), JSON.stringify(results.unmatched.map(e => ({fromEmail: e.fromEmail, subject: e.subject, id: e.id})), null, 2));

console.log('\nBatch files saved to', dir);
