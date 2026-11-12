// A small S3 client for the asset uploader (upload-assets.mjs): AWS Signature
// Version 4 by hand and Node's fetch, no dependencies. Path-style requests
// (`<endpoint>/<bucket>/<key>`), which Cloudflare R2, AWS S3 and MinIO all
// accept. Only what the uploader and the pruner need: list keys, head, get,
// put, delete.
//
// Importing this module has no side effects.
import { createHash, createHmac } from "node:crypto";

export const EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

export const sha256Hex = (data) => createHash("sha256").update(data).digest("hex");
const hmac = (key, data) => createHmac("sha256", key).update(data).digest();

/** RFC 3986 percent-encoding: everything but A-Z a-z 0-9 - . _ ~ (UTF-8 bytes). */
export function encodeRfc3986(s) {
  return encodeURIComponent(s).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
}

/** The canonical URI of a decoded path: each segment encoded, slashes kept (S3 does not normalize). */
export function canonicalUri(path) {
  return path.split("/").map(encodeRfc3986).join("/") || "/";
}

/** The canonical query string of decoded `[name, value]` pairs: encoded, sorted by name then value. */
export function canonicalQuery(query) {
  return query
    .map(([k, v]) => [encodeRfc3986(k), encodeRfc3986(v ?? "")])
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0))
    .map(([k, v]) => `${k}=${v}`)
    .join("&");
}

/** `20150830T123600Z` for a Date. */
export function amzDate(date) {
  return date.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
}

/**
 * Signs one request (AWS Signature Version 4, header form). `path` and
 * `query` are decoded; `headers` are the ones to send and sign besides
 * `host` and `x-amz-date` (added here). Returns the headers to send with
 * `authorization`, and the intermediate strings (for tests).
 */
export function signRequest({ method, host, path, query = [], headers = {}, payloadHash, region, service, accessKeyId, secretAccessKey, date = new Date() }) {
  const stamp = amzDate(date);
  const day = stamp.slice(0, 8);
  const all = { ...Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), String(v)])), host, "x-amz-date": stamp };
  const names = Object.keys(all).sort();
  const canonicalHeaders = names.map((n) => `${n}:${all[n].trim().replace(/\s+/g, " ")}\n`).join("");
  const signedHeaders = names.join(";");
  const canonicalRequest = [method, canonicalUri(path), canonicalQuery(query), canonicalHeaders, signedHeaders, payloadHash].join("\n");
  const scope = `${day}/${region}/${service}/aws4_request`;
  const stringToSign = ["AWS4-HMAC-SHA256", stamp, scope, sha256Hex(canonicalRequest)].join("\n");
  const key = hmac(hmac(hmac(hmac(`AWS4${secretAccessKey}`, day), region), service), "aws4_request");
  const signature = createHmac("sha256", key).update(stringToSign).digest("hex");
  const authorization = `AWS4-HMAC-SHA256 Credential=${accessKeyId}/${scope}, SignedHeaders=${signedHeaders}, Signature=${signature}`;
  const out = { ...all, authorization };
  delete out.host; // fetch sets it from the URL
  return { headers: out, canonicalRequest, stringToSign, signature };
}

const XML_ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };
const unxml = (s) => s.replace(/&(amp|lt|gt|quot|apos|#\d+|#x[0-9a-f]+);/gi, (_, e) => (e[0] === "#" ? String.fromCodePoint(e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10)) : XML_ENTITIES[e]));

/** The keys, truncation flag and continuation token of a ListObjectsV2 answer. */
export function parseListObjects(xml) {
  const keys = [...xml.matchAll(/<Key>([\s\S]*?)<\/Key>/g)].map((m) => unxml(m[1]));
  const truncated = /<IsTruncated>\s*true\s*<\/IsTruncated>/i.test(xml);
  const token = /<NextContinuationToken>([\s\S]*?)<\/NextContinuationToken>/.exec(xml)?.[1];
  return { keys, truncated, token: token === undefined ? null : unxml(token) };
}

export class S3Error extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

/** The error code and message of an S3 XML error body, for messages. */
function errorText(body) {
  const code = /<Code>([\s\S]*?)<\/Code>/.exec(body)?.[1];
  const msg = /<Message>([\s\S]*?)<\/Message>/.exec(body)?.[1];
  return code ? `${code}${msg ? `: ${unxml(msg)}` : ""}` : body.slice(0, 200);
}

export class S3Client {
  /**
   * `endpoint`: `https://<account id>.r2.cloudflarestorage.com` (R2),
   * `https://s3.<region>.amazonaws.com` (AWS), `http://127.0.0.1:9000` (MinIO).
   */
  constructor({ endpoint, bucket, region = "auto", accessKeyId, secretAccessKey, fetch: fetchFn = globalThis.fetch, now = () => new Date() }) {
    const url = new URL(endpoint);
    if (url.protocol !== "https:" && url.protocol !== "http:") throw new Error(`the S3 endpoint must be an http(s) URL, not ${endpoint}`);
    if (!bucket) throw new Error("no bucket named");
    if (!accessKeyId || !secretAccessKey) throw new Error("the S3 access key id and secret are needed");
    this.origin = url.origin;
    this.host = url.host;
    this.prefix = url.pathname.replace(/\/+$/, "");
    this.bucket = bucket;
    this.region = region;
    this.creds = { accessKeyId, secretAccessKey };
    this.fetch = fetchFn;
    this.now = now;
  }

  /** One signed request for `key` (`""`: the bucket itself). */
  async request(method, key, { query = [], headers = {}, body } = {}) {
    const path = `${this.prefix}/${this.bucket}${key ? `/${key}` : ""}`;
    const payloadHash = body ? sha256Hex(body) : EMPTY_SHA256;
    const signed = signRequest({
      method,
      host: this.host,
      path,
      query,
      headers: { ...headers, "x-amz-content-sha256": payloadHash },
      payloadHash,
      region: this.region,
      service: "s3",
      ...this.creds,
      date: this.now(),
    });
    const qs = canonicalQuery(query);
    const url = `${this.origin}${canonicalUri(path)}${qs ? `?${qs}` : ""}`;
    return this.fetch(url, { method, headers: signed.headers, body });
  }

  /** Every key under `prefix` (ListObjectsV2, 1000 a page). */
  async listKeys(prefix = "") {
    const keys = new Set();
    let token = null;
    do {
      const query = [["list-type", "2"], ["max-keys", "1000"]];
      if (prefix) query.push(["prefix", prefix]);
      if (token) query.push(["continuation-token", token]);
      const res = await this.request("GET", "", { query });
      const text = await res.text();
      if (!res.ok) throw new S3Error(`listing ${this.bucket}/${prefix}: ${res.status} ${errorText(text)}`, res.status, text);
      const page = parseListObjects(text);
      for (const k of page.keys) keys.add(k);
      token = page.truncated ? page.token : null;
      if (page.truncated && !token) throw new S3Error(`listing ${this.bucket}/${prefix}: truncated without a continuation token`, res.status, text);
    } while (token);
    return keys;
  }

  /** Whether `key` exists (HEAD). */
  async exists(key) {
    const res = await this.request("HEAD", key);
    if (res.status === 404) return false;
    if (!res.ok) throw new S3Error(`HEAD ${key}: ${res.status}`, res.status, "");
    return true;
  }

  /**
   * Uploads `body` as `key` unless it exists (`If-None-Match: *`; a service
   * that does not support the condition is asked again without it).
   * "uploaded", or "exists" when the key was already there.
   */
  async putNew(key, body, headers = {}) {
    let res = await this.request("PUT", key, { headers: { ...headers, "if-none-match": "*" }, body });
    if (res.status === 501 || (res.status === 400 && /NotImplemented|InvalidArgument/.test(await res.clone().text()))) {
      if (await this.exists(key)) return "exists";
      res = await this.request("PUT", key, { headers, body });
    }
    if (res.status === 412) return "exists";
    if (!res.ok) {
      const text = await res.text();
      throw new S3Error(`PUT ${key}: ${res.status} ${errorText(text)}`, res.status, text);
    }
    await res.arrayBuffer();
    return "uploaded";
  }

  /** Uploads `body` as `key`, replacing what is there. */
  async put(key, body, headers = {}) {
    const res = await this.request("PUT", key, { headers, body });
    if (!res.ok) {
      const text = await res.text();
      throw new S3Error(`PUT ${key}: ${res.status} ${errorText(text)}`, res.status, text);
    }
    await res.arrayBuffer();
  }

  /** The body of `key` as text; `null` when it does not exist. */
  async getText(key) {
    const res = await this.request("GET", key);
    if (res.status === 404) return null;
    const text = await res.text();
    if (!res.ok) throw new S3Error(`GET ${key}: ${res.status} ${errorText(text)}`, res.status, text);
    return text;
  }

  /** Deletes `key` (deleting a missing key succeeds too). */
  async delete(key) {
    const res = await this.request("DELETE", key);
    if (!res.ok && res.status !== 404) {
      const text = await res.text();
      throw new S3Error(`DELETE ${key}: ${res.status} ${errorText(text)}`, res.status, text);
    }
    await res.arrayBuffer();
  }
}
