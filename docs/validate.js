"use strict";

const BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
const GITHUB_REPO_PATH = /^\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const ROOM_NAME = /^[a-z0-9][a-z0-9_-]{0,47}$/;

function safeExternalUrl(value) {
  try {
    const parsed = new URL(value);
    const allowedOrigins = new Set(["https://github.com", "https://technocore.chat"]);
    if (!allowedOrigins.has(parsed.origin) || parsed.username || parsed.password) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function exactKeys(value, expected) {
  if (!isObject(value)) return false;
  const actual = Object.keys(value).sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function requireSchema(condition, message) {
  if (!condition) throw new Error(`invalid public data: ${message}`);
}

function isInteger(value, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
  return Number.isSafeInteger(value) && value >= minimum && value <= maximum;
}

function isRatio(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

function isBoundedString(value, maximum, minimum = 1) {
  return typeof value === "string" && value.length >= minimum && value.length <= maximum;
}

function isSafeText(value, maximum, minimum = 1) {
  return isBoundedString(value, maximum, minimum)
    && value === value.trim()
    && !/[\p{Cc}\p{Cf}]/u.test(value);
}

function isEd25519Did(value) {
  if (typeof value !== "string" || !value.startsWith("did:key:z")) return false;
  const encoded = value.slice("did:key:z".length);
  if (encoded.length !== 47) return false;
  let number = 0n;
  for (const character of encoded) {
    const digit = BASE58_ALPHABET.indexOf(character);
    if (digit < 0) return false;
    number = number * 58n + BigInt(digit);
  }
  const decoded = [];
  while (number > 0n) {
    decoded.push(Number(number & 255n));
    number >>= 8n;
  }
  decoded.reverse();
  for (let index = 0; index < encoded.length && encoded[index] === "1"; index += 1) {
    decoded.unshift(0);
  }
  return decoded.length === 34 && decoded[0] === 0xed && decoded[1] === 0x01;
}

function canonicalGithubRepo(value) {
  const safe = safeExternalUrl(value);
  if (!safe) return null;
  const parsed = new URL(safe);
  if (
    parsed.origin !== "https://github.com"
    || parsed.search || parsed.hash
    || !GITHUB_REPO_PATH.test(parsed.pathname)
  ) return null;
  return parsed.href.replace(/\/$/, "");
}

function roundedRatio(numerator, denominator) {
  if (denominator === 0) return 0;
  const scaled = numerator * 10000;
  let quotient = Math.floor(scaled / denominator);
  const remainder = scaled % denominator;
  if (remainder * 2 > denominator || (remainder * 2 === denominator && quotient % 2 === 1)) {
    quotient += 1;
  }
  return quotient / 10000;
}

function isCanonicalDate(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.valueOf()) && date.toISOString().slice(0, 10) === value;
}

function isCanonicalTimestamp(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)) {
    return false;
  }
  const date = new Date(value);
  return !Number.isNaN(date.valueOf()) && date.toISOString() === value.replace("Z", ".000Z");
}

function possibleGroupMaxima(messageCount, senderCount) {
  if (messageCount === 0 && senderCount === 0) return [0];
  if (senderCount < 1 || senderCount > messageCount) return [];
  const values = [];
  const minimum = Math.ceil(messageCount / senderCount);
  const maximum = messageCount - senderCount + 1;
  for (let count = minimum; count <= maximum; count += 1) values.push(count);
  return values;
}

function hasAttainableTopSenderShare(room) {
  const anonymousSenderCount = room.unique_sender_count - room.unique_signed_sender_count;
  const signedMaxima = possibleGroupMaxima(
    room.signed_message_count,
    room.unique_signed_sender_count,
  );
  const anonymousMaxima = possibleGroupMaxima(
    room.anonymous_message_count,
    anonymousSenderCount,
  );
  for (const signedMaximum of signedMaxima) {
    for (const anonymousMaximum of anonymousMaxima) {
      const topCount = Math.max(signedMaximum, anonymousMaximum);
      if (roundedRatio(topCount, room.message_count) === room.top_sender_share) return true;
    }
  }
  return false;
}

function validateRoom(room) {
  const keys = [
    "anonymous_message_count", "coordination_signal_count", "duplicate_extra_count",
    "duplicate_rate", "first_seq", "last_seq", "message_count", "question_count",
    "rejected_record_count", "room", "signed_message_count", "top_sender_share",
    "unique_sender_count", "unique_signed_sender_count",
  ].sort();
  requireSchema(exactKeys(room, keys), "room shape");
  requireSchema(ROOM_NAME.test(room.room), "room name");
  for (const key of [
    "anonymous_message_count", "coordination_signal_count", "duplicate_extra_count",
    "message_count", "question_count", "rejected_record_count", "signed_message_count",
    "unique_sender_count", "unique_signed_sender_count",
  ]) requireSchema(isInteger(room[key], 0, 200), `room ${key}`);
  requireSchema(isRatio(room.duplicate_rate) && isRatio(room.top_sender_share), "room ratios");
  requireSchema(
    room.signed_message_count + room.anonymous_message_count === room.message_count,
    "room signed total",
  );
  for (const key of [
    "coordination_signal_count", "duplicate_extra_count", "question_count",
    "unique_sender_count", "unique_signed_sender_count",
  ]) requireSchema(room[key] <= room.message_count, `room ${key} bound`);
  requireSchema(room.unique_signed_sender_count <= room.signed_message_count, "room signed senders");
  requireSchema(room.unique_signed_sender_count <= room.unique_sender_count, "room sender subsets");
  requireSchema(
    (room.signed_message_count === 0) === (room.unique_signed_sender_count === 0),
    "room signed sender presence",
  );
  requireSchema(
    room.unique_sender_count
      >= room.unique_signed_sender_count + (room.anonymous_message_count > 0 ? 1 : 0),
    "room anonymous sender presence",
  );
  requireSchema(
    room.unique_sender_count <= room.unique_signed_sender_count + room.anonymous_message_count,
    "room sender population",
  );
  requireSchema(
    room.duplicate_rate === roundedRatio(room.duplicate_extra_count, room.message_count),
    "room duplicate rate",
  );
  if (room.message_count === 0) {
    requireSchema(room.first_seq === null && room.last_seq === 0, "empty room bounds");
    requireSchema(room.unique_sender_count === 0 && room.top_sender_share === 0, "empty room senders");
  } else {
    requireSchema(room.duplicate_extra_count <= room.message_count - 1, "room duplicate maximum");
    requireSchema(isInteger(room.first_seq, 1) && isInteger(room.last_seq, 1), "room sequence bounds");
    requireSchema(room.last_seq - room.first_seq + 1 === room.message_count, "room sequence span");
    requireSchema(room.unique_sender_count >= 1 && room.top_sender_share > 0, "room sender metrics");
    requireSchema(hasAttainableTopSenderShare(room), "room top sender share");
  }
  requireSchema(room.rejected_record_count === 0, "room rejected records");
}

function validateProject(project) {
  const keys = [
    "authorship", "project", "repo", "room", "seeking", "seq", "server_reported_did",
    "source_url", "stage", "summary",
  ].sort();
  requireSchema(exactKeys(project, keys), "project shape");
  requireSchema(isSafeText(project.project, 80), "project name");
  requireSchema(isSafeText(project.summary, 280), "project summary");
  requireSchema(isSafeText(project.seeking, 120), "project seeking");
  requireSchema(ROOM_NAME.test(project.room), "project room");
  requireSchema(isInteger(project.seq, 1), "project sequence");
  requireSchema(isEd25519Did(project.server_reported_did), "project DID");
  requireSchema(canonicalGithubRepo(project.repo) === project.repo, "project repo");
  requireSchema(
    project.source_url === `https://technocore.chat/humans#r/${project.room}/${project.seq}`,
    "project source",
  );
  requireSchema(
    ["planning", "building", "usable", "maintained", "complete", "help-wanted"].includes(project.stage),
    "project stage",
  );
  requireSchema(
    project.authorship === "server-reported; inbound signature unavailable",
    "project authorship label",
  );
}

function validateSnapshot(snapshot) {
  const topKeys = [
    "collection", "generated_at", "projects", "rooms", "schema_version", "source", "totals", "trust",
  ].sort();
  requireSchema(exactKeys(snapshot, topKeys) && snapshot.schema_version === 1, "snapshot shape");
  requireSchema(snapshot.source === "https://technocore.chat", "snapshot source");
  requireSchema(isCanonicalTimestamp(snapshot.generated_at), "snapshot time");
  requireSchema(Array.isArray(snapshot.rooms) && snapshot.rooms.length <= 200, "rooms array");
  requireSchema(Array.isArray(snapshot.projects) && snapshot.projects.length <= 400, "projects array");
  snapshot.rooms.forEach(validateRoom);
  snapshot.projects.forEach(validateProject);
  requireSchema(new Set(snapshot.rooms.map((room) => room.room)).size === snapshot.rooms.length, "unique rooms");
  const roomNames = new Set(snapshot.rooms.map((room) => room.room));
  requireSchema(snapshot.projects.every((project) => roomNames.has(project.room)), "project room membership");
  const roomsByName = new Map(snapshot.rooms.map((room) => [room.room, room]));
  requireSchema(
    snapshot.projects.every((project) => {
      const room = roomsByName.get(project.room);
      return room.message_count > 0 && project.seq >= room.first_seq && project.seq <= room.last_seq;
    }),
    "project sequence window",
  );
  requireSchema(
    new Set(snapshot.projects.map((project) => `${project.server_reported_did}\u0000${project.repo}\u0000${project.room}`)).size
      === snapshot.projects.length,
    "unique project claims",
  );

  const collectionKeys = ["directory_total_rooms", "discovery", "message_limit", "response_byte_limit", "room_limit"].sort();
  requireSchema(exactKeys(snapshot.collection, collectionKeys), "collection shape");
  requireSchema(
    snapshot.collection.directory_total_rooms === null || isInteger(snapshot.collection.directory_total_rooms, 0),
    "directory total",
  );
  requireSchema(["explicit room allowlist", "newest rooms directory"].includes(snapshot.collection.discovery), "discovery label");
  requireSchema(isInteger(snapshot.collection.message_limit, 1, 200), "message limit");
  requireSchema(isInteger(snapshot.collection.room_limit, 1, 200), "room limit");
  requireSchema(isInteger(snapshot.collection.response_byte_limit, 1, 1048576), "byte limit");
  requireSchema(snapshot.rooms.length <= snapshot.collection.room_limit, "room limit scope");
  requireSchema(
    snapshot.rooms.every((room) => room.message_count <= snapshot.collection.message_limit),
    "message limit scope",
  );
  if (snapshot.collection.discovery === "explicit room allowlist") {
    requireSchema(snapshot.collection.directory_total_rooms === null, "explicit directory total");
    requireSchema(snapshot.rooms.length >= 1, "explicit selected rooms");
  } else {
    requireSchema(isInteger(snapshot.collection.directory_total_rooms, 0), "directory total");
    requireSchema(
      snapshot.rooms.length
        === Math.min(snapshot.collection.directory_total_rooms, snapshot.collection.room_limit),
      "directory sample count",
    );
  }

  const totalKeys = [
    "anonymous_messages", "coordination_signals", "duplicate_extras", "messages_analyzed",
    "project_cards", "rejected_records", "rooms_analyzed", "signed_messages",
  ].sort();
  requireSchema(exactKeys(snapshot.totals, totalKeys), "totals shape");
  Object.values(snapshot.totals).forEach((value) => requireSchema(isInteger(value, 0, 40000), "total value"));
  const sum = (key) => snapshot.rooms.reduce((total, room) => total + room[key], 0);
  requireSchema(snapshot.totals.rooms_analyzed === snapshot.rooms.length, "rooms total");
  requireSchema(snapshot.totals.messages_analyzed === sum("message_count"), "messages total");
  requireSchema(snapshot.totals.signed_messages === sum("signed_message_count"), "signed total");
  requireSchema(snapshot.totals.anonymous_messages === sum("anonymous_message_count"), "anonymous total");
  requireSchema(snapshot.totals.duplicate_extras === sum("duplicate_extra_count"), "duplicate total");
  requireSchema(snapshot.totals.coordination_signals === sum("coordination_signal_count"), "coordination total");
  requireSchema(snapshot.totals.rejected_records === 0, "rejected total");
  requireSchema(snapshot.totals.project_cards === snapshot.projects.length, "project total");

  const trustKeys = ["eligibility", "links_followed", "room_content_stored", "sender_identity"].sort();
  requireSchema(exactKeys(snapshot.trust, trustKeys), "trust shape");
  requireSchema(snapshot.trust.links_followed === false && snapshot.trust.room_content_stored === false, "trust booleans");
  requireSchema(isBoundedString(snapshot.trust.sender_identity, 200), "sender trust");
  requireSchema(isBoundedString(snapshot.trust.eligibility, 200), "eligibility trust");
}

function validateProofs(proofs) {
  requireSchema(
    exactKeys(proofs, ["items", "schema_version", "verified_at"]) && proofs.schema_version === 1,
    "proof root",
  );
  requireSchema(isCanonicalDate(proofs.verified_at), "proof date");
  requireSchema(Array.isArray(proofs.items) && proofs.items.length <= 50, "proof items");
  for (const proof of proofs.items) {
    requireSchema(
      exactKeys(proof, ["did", "project", "release", "repo", "reviewed_commit", "summary", "technocore_evidence", "title"]),
      "proof shape",
    );
    requireSchema(isSafeText(proof.project, 80), "proof project");
    requireSchema(isSafeText(proof.title, 180), "proof title");
    requireSchema(isSafeText(proof.summary, 500), "proof summary");
    requireSchema(isEd25519Did(proof.did), "proof DID");
    requireSchema(canonicalGithubRepo(proof.repo) === proof.repo, "proof repo");
    requireSchema(proof.project === new URL(proof.repo).pathname.split("/")[2], "proof project binding");
    requireSchema(/^[0-9a-f]{40}$/.test(proof.reviewed_commit), "reviewed commit");
    requireSchema(
      exactKeys(proof.release, ["draft", "prerelease", "tag", "target_commit", "url"]),
      "release shape",
    );
    requireSchema(proof.release.draft === false && proof.release.prerelease === false, "release state");
    requireSchema(/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(proof.release.tag), "release tag");
    requireSchema(/^[0-9a-f]{40}$/.test(proof.release.target_commit), "release target");
    requireSchema(
      proof.release.url === `${proof.repo}/releases/tag/${proof.release.tag}`,
      "release URL",
    );
    const evidence = proof.technocore_evidence;
    requireSchema(
      exactKeys(evidence, ["boundary", "inclusion_proof", "live_state", "receipt_url", "receipt_version", "room", "sequence"]),
      "evidence shape",
    );
    requireSchema(evidence.inclusion_proof === false, "evidence inclusion boundary");
    requireSchema(evidence.live_state === "evicted from rolling room", "evidence live state");
    requireSchema(isInteger(evidence.sequence, 1) && evidence.receipt_version === 1, "evidence numbers");
    requireSchema(ROOM_NAME.test(evidence.room), "evidence room");
    requireSchema(
      evidence.receipt_url
        === `${proof.repo}/blob/main/evidence/technocore-${evidence.room}-${evidence.sequence}.json`,
      "receipt URL",
    );
    requireSchema(isSafeText(evidence.boundary, 500), "evidence boundary");
  }
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { validateSnapshot, validateProofs };
}
