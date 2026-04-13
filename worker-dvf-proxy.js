export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Headers": "Content-Type",
          "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS"
        }
      });
    }

    const url = new URL(request.url);
    const CORS_H = {
      "Access-Control-Allow-Origin": "*",
      "Content-Type": "application/json"
    };

    // ========== GITHUB PROXY ==========
    if (url.pathname.startsWith("/github/")) {
      let ghUrl;
      const afterGithub = url.pathname.replace("/github/", "");

      if (afterGithub.startsWith("contents/")) {
        ghUrl = "https://api.github.com/repos/Bencode92/studyforge/" + afterGithub;
      } else {
        ghUrl = "https://api.github.com/repos/" + afterGithub;
      }

      const ghHeaders = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": "token " + env.GITHUB_TOKEN,
        "Content-Type": "application/json",
        "User-Agent": "StructBoard-Worker"
      };

      let resp;
      if (request.method === "GET") {
        resp = await fetch(ghUrl, { headers: ghHeaders });
      } else if (request.method === "PUT") {
        resp = await fetch(ghUrl, { method: "PUT", headers: ghHeaders, body: await request.text() });
      } else if (request.method === "DELETE") {
        resp = await fetch(ghUrl, { method: "DELETE", headers: ghHeaders, body: await request.text() });
      } else {
        return new Response(JSON.stringify({ error: "Method not allowed" }), { status: 405, headers: CORS_H });
      }

      return new Response(await resp.text(), { status: resp.status, headers: CORS_H });
    }

    // ========== DVF API PROXY ==========
    if (url.pathname.startsWith("/dvf/")) {
      const dep = url.searchParams.get("dep");
      if (!dep) {
        return new Response(JSON.stringify({ error: "Missing dep parameter" }), { status: 400, headers: CORS_H });
      }
      try {
        const dvfUrl = "https://dvf-api.data.gouv.fr/dvf/csv/?dep=" + encodeURIComponent(dep);
        const resp = await fetch(dvfUrl);
        return new Response(await resp.text(), {
          status: resp.status,
          headers: {
            "Access-Control-Allow-Origin": "*",
            "Content-Type": "text/csv"
          }
        });
      } catch (e) {
        return new Response(JSON.stringify({ error: e.message }), { status: 502, headers: CORS_H });
      }
    }

    // ========== TWELVE DATA PROXY ==========
    if (url.pathname.startsWith("/twelvedata/")) {
      const tdPath = url.pathname.replace("/twelvedata/", "");
      const params = url.search
        ? url.search + "&apikey=" + env.TWELVE_DATA_API_KEY
        : "?apikey=" + env.TWELVE_DATA_API_KEY;
      const tdUrl = "https://api.twelvedata.com/" + tdPath + params;

      try {
        const resp = await fetch(tdUrl);
        return new Response(await resp.text(), { headers: CORS_H });
      } catch (e) {
        return new Response(JSON.stringify({ status: "error", message: e.message }), { status: 502, headers: CORS_H });
      }
    }

    // ========== CLAUDE PROXY ==========
    if (request.method === "POST") {
      const body = await request.text();
      if (!body) {
        return new Response(JSON.stringify({ error: "No body" }), { status: 400, headers: CORS_H });
      }
      const resp = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": env.ANTHROPIC_API_KEY,
          "anthropic-version": "2023-06-01"
        },
        body
      });
      return new Response(await resp.text(), { headers: CORS_H });
    }

    return new Response("Proxy OK", { status: 200 });
  }
};
