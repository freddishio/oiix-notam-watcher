const notamDecoder = require('./notam-decoder.js');
const fs = require('fs');

const inputPath = process.argv[2];
if (!inputPath) {
    console.error(JSON.stringify({error: "No input file provided"}));
    process.exit(1);
}

try {
    const rawNotam = fs.readFileSync(inputPath, 'utf8');
    const decoded = notamDecoder.decode(rawNotam);
    console.log(JSON.stringify(decoded));
} catch (e) {
    console.error(JSON.stringify({error: e.toString()}));
    process.exit(1);
}
