export async function workspaceRequest(path, options = {}) {
  let response
  try {
    response = await fetch(path, options)
  } catch (error) {
    throw new Error(`Cannot reach the ContextPack API. ${error.message}`)
  }
  const text = await response.text()
  let data
  try {
    data = JSON.parse(text)
  } catch {
    const detail = !text || /^\s*</.test(text)
      ? 'The ContextPack API is unavailable. Start the backend and try again.'
      : text.slice(0, 300)
    throw new Error(`HTTP ${response.status}: ${detail}`)
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('The ContextPack API returned an invalid response.')
  if (!response.ok || data.success === false) {
    const detail = data.detail || data.error || data
    throw new Error(`HTTP ${response.status}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`)
  }
  return data
}

export function jsonRequest(method, body) {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
}

export function sourceLabel(source) {
  return source.original_filename || source.title || source.id
}
