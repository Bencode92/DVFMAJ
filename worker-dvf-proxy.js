// ========== DVF API PROXY ==========
// Ajouter ce bloc AVANT le "// ========== CLAUDE PROXY ==========" dans ton Worker Cloudflare
// URL: https://studyforge-proxy.benoit-comas.workers.dev/dvf/?dep=92

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
