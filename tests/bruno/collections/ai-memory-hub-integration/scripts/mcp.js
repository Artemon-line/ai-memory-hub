function eventPayload(body) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  const line = text.split(/\r?\n/).find((item) => {
    if (!item.startsWith("data: ")) {
      return false;
    }
    const payload = JSON.parse(item.slice("data: ".length));
    return Boolean(payload.result);
  });
  if (!line) {
    throw new Error("No MCP result event found");
  }
  return JSON.parse(line.slice("data: ".length));
}

function toolPayload(body) {
  const envelope = eventPayload(body);
  const content = envelope.result.content;
  if (!Array.isArray(content) || content.length === 0) {
    throw new Error("MCP tool result did not include content");
  }
  return JSON.parse(content[0].text);
}

function responseHeader(headers, name) {
  const wanted = name.toLowerCase();
  for (const [key, value] of Object.entries(headers || {})) {
    if (key.toLowerCase() === wanted) {
      return Array.isArray(value) ? value[0] : value;
    }
  }
  return undefined;
}

module.exports = {
  eventPayload,
  responseHeader,
  toolPayload,
};
