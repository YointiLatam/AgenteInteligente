// Fetch wrapper. The JWT lives only in this closure (memory): never in storage or cookies.
import { errorInfo } from "./render.js";

export class ApiError extends Error {
  constructor(status, detail) {
    super(errorInfo(status).message);
    this.status = status;
    this.kind = errorInfo(status).kind;
    this.detail = typeof detail === "string" ? detail : null;
  }
}

export function createApi({ fetchImpl = (...args) => fetch(...args), onUnauthorized = () => {} } = {}) {
  let token = null;

  async function parse(response) {
    try {
      return await response.json();
    } catch {
      return null;
    }
  }

  async function request(method, path, { body, headers = {}, auth = true } = {}) {
    const finalHeaders = { Accept: "application/json", ...headers };
    if (auth && token) finalHeaders.Authorization = `Bearer ${token}`;
    if (body !== undefined) finalHeaders["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetchImpl(path, {
        method,
        headers: finalHeaders,
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: "omit",
        cache: "no-store",
      });
    } catch {
      throw new ApiError(0, null);
    }
    const data = await parse(response);
    if (!response.ok) {
      if (response.status === 401 && auth) {
        token = null;
        onUnauthorized();
      }
      throw new ApiError(response.status, data && data.detail);
    }
    return data;
  }

  return {
    hasSession: () => token !== null,
    /** Exchanges the API key for a JWT; the key is not retained anywhere. */
    async login(apiKey) {
      const data = await request("POST", "/auth/token", { headers: { "X-API-Key": apiKey }, auth: false });
      token = data.access_token;
    },
    logout() {
      token = null;
    },
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, { body }),
  };
}
