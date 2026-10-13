"use strict";

const fs = require("fs");
const path = require("path");
const assert = require("assert");
const {
  validateSnapshot,
  validateProofs,
} = require(path.join(__dirname, "..", "docs", "validate.js"));

const root = path.join(__dirname, "..");
const readJson = (relative) => JSON.parse(fs.readFileSync(path.join(root, relative), "utf8"));
const clone = (value) => JSON.parse(JSON.stringify(value));
const rejects = (operation, label) => assert.throws(operation, /invalid public data/, label);

const snapshot = readJson("docs/data/snapshot.json");
const proofs = readJson("docs/data/proofs.json");
validateSnapshot(snapshot);
validateProofs(proofs);

const badDid = clone(proofs);
badDid.items[0].did = "did:key:z6MkTUbu3zy4wX6t16umPZ9c8qKrqfRvBxywbdGRYhJy9rQ8";
rejects(() => validateProofs(badDid), "non-Ed25519 multicodec must fail");

const withProject = clone(snapshot);
withProject.projects = [{
  project: "Technocore Hermes",
  summary: "A safe client.",
  repo: "https://github.com/snoodoodles/technocore-hermes",
  stage: "maintained",
  seeking: "Reviewers",
  server_reported_did: proofs.items[0].did,
  room: withProject.rooms[0].room,
  seq: withProject.rooms[0].first_seq,
  source_url: `https://technocore.chat/humans#r/${withProject.rooms[0].room}/${withProject.rooms[0].first_seq}`,
  authorship: "server-reported; inbound signature unavailable",
}];
withProject.totals.project_cards = 1;
validateSnapshot(withProject);

const unverifiedProject = clone(withProject);
unverifiedProject.projects[0].authorship = "server-reported; inbound signature not verified";
validateSnapshot(unverifiedProject);
const overstatedProject = clone(withProject);
overstatedProject.projects[0].authorship = "verified";
rejects(() => validateSnapshot(overstatedProject), "signature presence must not imply verification");

const badProjectDid = clone(withProject);
badProjectDid.projects[0].server_reported_did = badDid.items[0].did;
rejects(() => validateSnapshot(badProjectDid), "project DID multicodec must fail");

const badProjectSource = clone(withProject);
badProjectSource.projects[0].source_url = "https://technocore.chat/humans#r/lobby/1";
rejects(() => validateSnapshot(badProjectSource), "project source must bind room and sequence");

const projectOutsideWindow = clone(withProject);
projectOutsideWindow.projects[0].seq = withProject.rooms[0].first_seq - 1;
projectOutsideWindow.projects[0].source_url = `https://technocore.chat/humans#r/${withProject.rooms[0].room}/${projectOutsideWindow.projects[0].seq}`;
rejects(() => validateSnapshot(projectOutsideWindow), "project sequence must be inside room window");

const badRate = clone(snapshot);
badRate.rooms[0].duplicate_rate = 0;
rejects(() => validateSnapshot(badRate), "contradictory duplicate rate must fail");

const badCount = clone(snapshot);
badCount.rooms[0].coordination_signal_count = badCount.rooms[0].message_count + 1;
rejects(() => validateSnapshot(badCount), "coordination count above messages must fail");

const badTimestamp = clone(snapshot);
badTimestamp.generated_at = "2026-02-30T05:30:24Z";
rejects(() => validateSnapshot(badTimestamp), "timestamp must be a real canonical UTC date");

const impossibleDuplicates = clone(snapshot);
impossibleDuplicates.rooms[0].duplicate_extra_count = impossibleDuplicates.rooms[0].message_count;
impossibleDuplicates.rooms[0].duplicate_rate = 1;
impossibleDuplicates.totals.duplicate_extras = impossibleDuplicates.rooms.reduce(
  (total, room) => total + room.duplicate_extra_count,
  0,
);
rejects(() => validateSnapshot(impossibleDuplicates), "duplicate extras cannot equal nonempty count");

const impossibleTopShare = clone(snapshot);
impossibleTopShare.rooms[0].unique_sender_count = 1;
impossibleTopShare.rooms[0].unique_signed_sender_count = 1;
impossibleTopShare.rooms[0].top_sender_share = 0.1;
rejects(() => validateSnapshot(impossibleTopShare), "one sender requires full top share");

const tooSmallTopShare = clone(snapshot);
tooSmallTopShare.rooms[0].top_sender_share = 0.0001;
rejects(() => validateSnapshot(tooSmallTopShare), "top share must represent an attainable count");

const impossibleJointPopulation = clone(snapshot);
impossibleJointPopulation.rooms = [{
  room: "mixed",
  message_count: 4,
  rejected_record_count: 0,
  signed_message_count: 1,
  anonymous_message_count: 3,
  unique_sender_count: 2,
  unique_signed_sender_count: 1,
  top_sender_share: 0.5,
  duplicate_extra_count: 0,
  duplicate_rate: 0,
  question_count: 0,
  coordination_signal_count: 0,
  first_seq: 1,
  last_seq: 4,
}];
impossibleJointPopulation.projects = [];
impossibleJointPopulation.totals = {
  rooms_analyzed: 1,
  messages_analyzed: 4,
  rejected_records: 0,
  signed_messages: 1,
  anonymous_messages: 3,
  duplicate_extras: 0,
  coordination_signals: 0,
  project_cards: 0,
};
rejects(
  () => validateSnapshot(impossibleJointPopulation),
  "top share must be jointly attainable across signed and anonymous populations",
);

const impossibleSignedSenders = clone(snapshot);
impossibleSignedSenders.rooms[0].unique_signed_sender_count = 0;
rejects(() => validateSnapshot(impossibleSignedSenders), "signed messages require a signed sender");

const badRoomLimit = clone(snapshot);
badRoomLimit.collection.room_limit = 1;
rejects(() => validateSnapshot(badRoomLimit), "room array cannot exceed room limit");

const badMessageLimit = clone(snapshot);
badMessageLimit.collection.message_limit = 1;
rejects(() => validateSnapshot(badMessageLimit), "room messages cannot exceed message limit");

const badExplicitTotal = clone(snapshot);
badExplicitTotal.collection.directory_total_rooms = 2;
rejects(() => validateSnapshot(badExplicitTotal), "explicit mode requires null directory total");

const emptyExplicitScope = clone(snapshot);
emptyExplicitScope.rooms = [];
emptyExplicitScope.projects = [];
emptyExplicitScope.totals = {
  rooms_analyzed: 0,
  messages_analyzed: 0,
  rejected_records: 0,
  signed_messages: 0,
  anonymous_messages: 0,
  duplicate_extras: 0,
  coordination_signals: 0,
  project_cards: 0,
};
rejects(() => validateSnapshot(emptyExplicitScope), "explicit mode requires selected rooms");

const badDirectoryTotal = clone(snapshot);
badDirectoryTotal.collection.discovery = "newest rooms directory";
badDirectoryTotal.collection.directory_total_rooms = null;
rejects(() => validateSnapshot(badDirectoryTotal), "directory mode requires total");

const incompleteDirectory = clone(snapshot);
incompleteDirectory.collection.discovery = "newest rooms directory";
incompleteDirectory.collection.directory_total_rooms = 3;
incompleteDirectory.collection.room_limit = 3;
rejects(() => validateSnapshot(incompleteDirectory), "directory mode requires complete requested page");

const badRepo = clone(proofs);
badRepo.items[0].repo = "https://github.com/snoodoodles/technocore-hermes/extra";
rejects(() => validateProofs(badRepo), "noncanonical repository URL must fail");

const badRelease = clone(proofs);
badRelease.items[0].release.url = "https://github.com/snoodoodles/other/releases/tag/v0.2.0";
rejects(() => validateProofs(badRelease), "release URL must bind to repository and tag");

const badReceipt = clone(proofs);
badReceipt.items[0].technocore_evidence.receipt_url = "https://github.com/other/repo/blob/main/evidence.json";
rejects(() => validateProofs(badReceipt), "receipt URL must bind to repository");

const badReceiptSequence = clone(proofs);
badReceiptSequence.items[0].technocore_evidence.sequence += 1;
rejects(() => validateProofs(badReceiptSequence), "receipt path must bind room and sequence");

const empty = clone(snapshot);
empty.rooms = [{
  room: "empty",
  message_count: 0,
  rejected_record_count: 0,
  signed_message_count: 0,
  anonymous_message_count: 0,
  unique_sender_count: 0,
  unique_signed_sender_count: 0,
  top_sender_share: 0,
  duplicate_extra_count: 0,
  duplicate_rate: 0,
  question_count: 0,
  coordination_signal_count: 0,
  first_seq: null,
  last_seq: 0,
}];
empty.projects = [];
empty.totals = {
  rooms_analyzed: 1,
  messages_analyzed: 0,
  rejected_records: 0,
  signed_messages: 0,
  anonymous_messages: 0,
  duplicate_extras: 0,
  coordination_signals: 0,
  project_cards: 0,
};
validateSnapshot(empty);

const badEmpty = clone(empty);
badEmpty.rooms[0].last_seq = 1;
rejects(() => validateSnapshot(badEmpty), "empty room bounds must fail");

console.log("frontend semantic validation passed");
