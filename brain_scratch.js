const fs = require('fs');

let sectionData = {
    sections: [
        { type: "intro", start: 0, end: 16.56 },
        { type: "verse", start: 16.56, end: 32 },
        { type: "chorus", start: 32, end: 35 },
        { type: "verse", start: 35, end: 36.76 },
        { type: "chorus", start: 36.76, end: 85.3 }
    ]
};

const TOLERANCE = 0.5;
let splitTime = 34.0; // The chord is at exactly 34.0
let newType = "verse";

console.log("BEFORE:", sectionData.sections.map(s => `${s.start}-${s.end} ${s.type}`).join(" | "));

let sec = null;
let minDiff = TOLERANCE + 0.001;
for (let s of sectionData.sections) {
    let diff = Math.abs(s.start - splitTime);
    if (diff <= TOLERANCE && diff < minDiff) {
        minDiff = diff;
        sec = s;
    }
}

if (sec) {
    sec.type = newType;
    console.log("MATCHED BOUNDARY! Diff:", minDiff);
} else {
    // Normal split
    let parentSec = sectionData.sections.find(s => splitTime > s.start && splitTime < s.end);
    if (parentSec) {
        if (parentSec.type === newType) {
            console.log("SAME TYPE EXTENSION");
            sectionData.sections.sort((a,b) => a.start - b.start);
            let idx = sectionData.sections.indexOf(parentSec);
            if (idx > 0) {
                sectionData.sections[idx - 1].end = splitTime;
            } else {
                sectionData.sections.push({ type: "verse", start: parentSec.start, end: splitTime });
            }
            parentSec.start = splitTime;
            sectionData.sections.sort((a,b) => a.start - b.start);
        } else {
            console.log("SPLIT DIFFERENT TYPE");
            let newSec = {
                type: newType,
                start: splitTime,
                end: parentSec.end
            };
            parentSec.end = splitTime;
            sectionData.sections.push(newSec);
            sectionData.sections.sort((a,b) => a.start - b.start);
        }
    }
}

console.log("AFTER FRONTEND:", sectionData.sections.map(s => `${s.start}-${s.end} ${s.type}`).join(" | "));

// Simulate Backend Python merge
let final_sections = [];
for (let s of sectionData.sections) {
    if (final_sections.length > 0 && final_sections[final_sections.length - 1].type === s.type) {
        final_sections[final_sections.length - 1].end = Math.max(final_sections[final_sections.length - 1].end, s.end);
    } else {
        final_sections.push({...s});
    }
}
console.log("AFTER BACKEND MERGE:", final_sections.map(s => `${s.start}-${s.end} ${s.type}`).join(" | "));

// Simulate Frontend activeSec Logic
let ribbonTimes = [31.0, 34.0, 36.0, 38.0];
let lastSectionType = null;
let out = "";
for (let t of ribbonTimes) {
    let activeSec = final_sections.find(s => t >= s.start - 0.5 && t < s.end);
    if (activeSec && activeSec.type !== lastSectionType) {
        out += `HEADER[${activeSec.type}] `;
        lastSectionType = activeSec.type;
    }
    out += `CHORD[${t}] `;
}
console.log("RIBBON RENDER:", out);
