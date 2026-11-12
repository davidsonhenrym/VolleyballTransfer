const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
const element = {
    addEventListener() {},
    appendChild() {},
    classList: { add() {}, remove() {} },
    focus() {},
    remove() {},
    replaceChildren() {},
    scrollIntoView() {},
    style: {},
    textContent: "",
    value: ""
};
const context = {
    Blob,
    URL: { createObjectURL() { return "blob:test"; }, revokeObjectURL() {} },
    console,
    document: {
        body: element,
        createElement() { return { ...element }; },
        getElementById() { return element; }
    },
    fetch: async () => { throw new Error("fetch is not used by this parser test"); },
    setTimeout,
    window: { URL: { createObjectURL() { return "blob:test"; }, revokeObjectURL() {} } }
};
vm.createContext(context);
vm.runInContext(script, context);

const pasted = [
    "Status\tFlag\tFirst Name\tLast Name\tSchool\tWomen's Volleyball\tNew School\tConference",
    "Entered\tMT\tJane\tSmith\tSchool A\tWomen's Volleyball\tToledo\tMAC",
    "Entered\tMT\tAlice\tJones\tSchool B\tWomen's Volleyball\tDayton\tA-10",
    "Entered\tMT\tJane\tSmith\tSchool C\tWomen's Volleyball\tAkron\tMAC"
].join("\n");

const player = { Player: "Jane Smith", Team: "School A", Sets: "10", Matches: "5" };
const data = {
    byName: new Map([["jane smith", [player]]]),
    historyByName: new Map(),
    nameLengths: [2]
};
const candidates = context.parseNCAARows(pasted);
const results = candidates.map(candidate => context.matchCandidate(data, candidate));

assert.equal(candidates.length, 3, "header should be skipped and every athlete row retained");
assert.equal(results.filter(result => result.found).length, 2, "duplicate matching rows must be retained");
assert.equal(results.filter(result => !result.found).length, 1, "unmatched athlete rows must be retained");
assert.equal(results[1].query, "Alice Jones");

const suffix = index => String.fromCharCode(97 + Math.floor(index / 26)) + String.fromCharCode(97 + index % 26);
const fiftyRows = Array.from({ length: 50 }, (_, index) => {
    const id = suffix(index);
    return `Entered\tMT\tFirst${id}\tLast${id}\tSchool\tWomen's Volleyball\tNew School\tConference`;
});
const twentyEightPlayers = new Map(Array.from({ length: 28 }, (_, index) => {
    const id = suffix(index);
    const row = { Player: `First${id} Last${id}`, Team: "School", Sets: "10", Matches: "5" };
    return [`first${id} last${id}`, [row]];
}));
const fiftyCandidates = context.parseNCAARows(fiftyRows.join("\n"));
const fiftyResults = fiftyCandidates.map(candidate => context.matchCandidate({
    byName: twentyEightPlayers,
    historyByName: new Map(),
    nameLengths: [2]
}, candidate));

assert.equal(fiftyResults.length, 50);
assert.equal(fiftyResults.filter(result => result.found).length, 28);
assert.equal(fiftyResults.filter(result => !result.found).length, 22);

const realPlayers = context.parseCSV(
    fs.readFileSync(path.join(__dirname, "..", "data", "all_players.csv"), "utf8")
).records;
const realByName = context.groupByName(realPlayers);
const realRows = [...realByName.values()].map(rows => rows[0]).filter(row => row.Player.includes(" "));
const realPaste = realRows.map(row => {
    const firstSpace = row.Player.indexOf(" ");
    const first = row.Player.slice(0, firstSpace);
    const last = row.Player.slice(firstSpace + 1);
    return `Entered\tMT\t${first}\t${last}\tSchool\tWomen's Volleyball\tNew School\tConference`;
}).join("\n");
const realCandidates = context.parseNCAARows(realPaste);
const realResults = realCandidates.map(candidate => context.matchCandidate({
    byName: realByName,
    historyByName: new Map(),
    nameLengths: []
}, candidate));

assert.equal(realCandidates.length, realRows.length, "every real database name should parse from an NCAA-style row");
assert.equal(realResults.filter(result => result.found).length, realRows.length, "every real database name should match exactly");
console.log(`parser tests passed: 50-row retention and ${realRows.length} real player names matched`);
