(() => {
  const transient = new Set([400, 502, 503, 504]);
  function failure(response, path, detail, retryable=false) {
    const error = new Error(`${path} · HTTP ${response.status}: ${detail}`);
    error.retryable = retryable;
    return error;
  }
  async function readJSON(response, path) {
    const type = response.headers.get('content-type') || '';
    const raw = await response.text();
    if (!type.toLowerCase().includes('application/json')) {
      const detail = /^\s*</.test(raw) ? 'JSON 대신 HTML 오류 페이지가 반환되었습니다. UI 서버 주소와 포트 전달 연결을 확인하세요.' : 'JSON 응답을 받지 못했습니다.';
      throw failure(response, path, detail, !response.ok && transient.has(response.status));
    }
    let result;
    try { result = JSON.parse(raw); }
    catch { throw failure(response, path, '서버가 올바르지 않은 JSON을 반환했습니다.'); }
    if (!response.ok) throw failure(response, path, result?.error || response.statusText, result?.request_error === true && transient.has(response.status));
    return result;
  }
  async function requestJSON(path, data) {
    const isPost = data !== undefined;
    for (let attempt=0; ; attempt++) {
      let response;
      try {
        response = await fetch(path, isPost ? {method:'POST', headers:{'Content-Type':'application/json','X-Gemma-Request':'1'}, body:JSON.stringify(data)} : {cache:'no-store'});
      } catch (error) {
        if (!isPost && attempt===0 && error.name!=='AbortError') continue;
        throw new Error(`${path}: 서버 연결 실패 (${error.message})`);
      }
      try { return await readJSON(response, path); }
      catch (error) {
        // Only safe reads retry once. Never repeat inference or other mutations.
        if (!isPost && attempt===0 && error.retryable) continue;
        throw error;
      }
    }
  }
  async function requireStream(response, path) {
    if (!response.ok) await readJSON(response, path);
    if (!(response.headers.get('content-type') || '').toLowerCase().includes('application/x-ndjson')) {
      await response.body?.cancel();
      throw failure(response, path, '생성 스트림 대신 다른 응답이 반환되었습니다. UI 서버 주소와 포트 전달 연결을 확인하세요.');
    }
  }
  window.LabHTTP = {requestJSON, readJSON, requireStream};
})();
