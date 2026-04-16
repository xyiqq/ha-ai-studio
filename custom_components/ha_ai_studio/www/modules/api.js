const API_BASE = "/api/ha_ai_studio";

async function getAuthToken() {
  try {
    if (window.parent?.hassConnection) {
      const connection = await window.parent.hassConnection;
      if (connection?.auth) {
        if (connection.auth.expired) {
          await connection.auth.refreshAccessToken();
        }
        return connection.auth.accessToken;
      }
    }
  } catch (error) {
    console.error("Failed to obtain Home Assistant token", error);
  }
  return null;
}

async function fetchWithAuth(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  const token = await getAuthToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(url, {
    ...options,
    headers,
    credentials: "same-origin",
  });

  const text = await response.text();
  let json = {};
  if (text) {
    try {
      json = JSON.parse(text);
    } catch (error) {
      json = { message: text };
    }
  }

  if (!response.ok) {
    const message = json.message || json.error || `HTTP ${response.status}`;
    throw new Error(message);
  }

  return json;
}

export async function apiGet(action, params = {}) {
  const url = new URL(API_BASE, window.location.origin);
  url.searchParams.set("action", action);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  });
  return fetchWithAuth(url.toString());
}

export async function apiPost(action, payload = {}) {
  return fetchWithAuth(API_BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...payload }),
  });
}
