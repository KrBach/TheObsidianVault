// A small, safe Markdown subset for Tally's replies: **bold**, *italics*, `code`, [links](https://...),
// "- " lists and "#" headings (shown bold). Everything is escaped first, so no HTML from a reply runs.

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function inline(s) {
  const codes = [];
  s = s.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = esc(s)
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<i>$2</i>")
    .replace(/(^|[^_\w])_([^_\n]+)_(?!\w)/g, "$1<i>$2</i>");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codes[Number(i)])}</code>`);
}

export function renderMarkdown(text) {
  return text.split("\n").map((line) => {
    const h = line.match(/^#{1,6}\s+(.*)$/);
    if (h) return `<b>${inline(h[1])}</b>`;
    const li = line.match(/^(\s*)[-*]\s+(.*)$/);
    if (li) return `${li[1]}• ${inline(li[2])}`;
    return inline(line);
  }).join("\n");
}
