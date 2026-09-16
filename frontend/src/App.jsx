import { useState, useEffect, useRef, useCallback } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import Aurora from './components/Aurora'
import Sidebar from './components/Sidebar'
import AgentComposer from './components/AgentComposer'
import MessageBubble from './components/MessageBubble'
import AssistantCard from './components/AssistantCard'
import ContextInspector from './components/ContextInspector'
import { DEMO_ROLE, DEMO_TASK, formatElapsed } from './utils'
import { workspaceRequest, jsonRequest, sourceLabel } from './workspaceApi'

const SELECTED_WORKSPACE_KEY = 'contextpack.selectedWorkspace'
const ACCEPTED_FILES = '.pdf,.docx,.txt,.md,.json'
const MAX_FILE_BYTES = 10 * 1024 * 1024

export default function App() {
  const [workspaces, setWorkspaces] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const [drafts, setDrafts] = useState({})
  const [saveStatus, setSaveStatus] = useState({})
  const [busy, setBusy] = useState('Loading workspaces')
  const [issue, setIssue] = useState(null)
  const [uploadReports, setUploadReports] = useState([])

  const [mode, setMode] = useState('input')
  const [elapsed, setElapsed] = useState(0)
  const [result, setResult] = useState(null)
  const [messages, setMessages] = useState([])
  const [errorDetail, setErrorDetail] = useState('')

  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState('')
  const [inspectorVisible, setInspectorVisible] = useState(false)
  const [focusNameId, setFocusNameId] = useState('')

  const draftsRef = useRef({})

  // 브라우저를 켜둔 동안 workspace별 마지막 결과를 보관한다.
  const workspaceSessionRef = useRef({})

  const dirtyRef = useRef(new Set())
  const saveQueueRef = useRef({})
  const selectedRef = useRef('')
  const actionLockRef = useRef(false)
  const timerRef = useRef(null)
  const copyTimerRef = useRef(null)
  const messageListRef = useRef(null)
  const fileInputRef = useRef(null)
  const nameInputRef = useRef(null)

  const selected = workspaces.find(
    (item) => item.workspace_id === selectedId,
  )

  const draft =
    drafts[selectedId] || {
      name: '',
      role: '',
      task: '',
    }

  const sources = selected?.sources || []

  const disabled = Boolean(busy) || mode === 'processing'

  const sourceSummary = sources.length
    ? sources.map(sourceLabel).join(' · ')
    : 'No sources added'

  const clearResult = useCallback(() => {
    setMode('input')
    setResult(null)
    setMessages([])
    setErrorDetail('')
    setElapsed(0)
    setInspectorVisible(false)
    setCopied(false)
    setCopyError('')

    clearInterval(timerRef.current)
    clearTimeout(copyTimerRef.current)
  }, [])

  const invalidateWorkspaceSession = useCallback((id) => {
    if (!id) return

    delete workspaceSessionRef.current[id]
  }, [])

  const restoreWorkspaceSession = useCallback(
    (id) => {
      if (!id) {
        clearResult()
        return
      }

      const session = workspaceSessionRef.current[id]

      if (!session) {
        clearResult()
        return
      }

      setMode(session.mode)
      setMessages(session.messages || [])
      setResult(session.result || null)
      setElapsed(session.elapsed || 0)
      setErrorDetail(session.errorDetail || '')
      setInspectorVisible(Boolean(session.inspectorVisible))

      setCopied(false)
      setCopyError('')

      clearInterval(timerRef.current)
      clearTimeout(copyTimerRef.current)
    },
    [clearResult],
  )

  const rememberWorkspace = useCallback((workspace) => {
    const id = workspace.workspace_id

    setWorkspaces((previous) =>
      previous.some((item) => item.workspace_id === id)
        ? previous.map((item) =>
            item.workspace_id === id ? workspace : item,
          )
        : [...previous, workspace],
    )

    // 서버에서 늦게 온 응답이 사용자의 아직 저장되지 않은 draft를
    // 덮어쓰지 않도록 한다.
    if (!draftsRef.current[id]) {
      const next = {
        name: workspace.name,
        role: workspace.role || '',
        task: workspace.task || '',
      }

      draftsRef.current = {
        ...draftsRef.current,
        [id]: next,
      }

      setDrafts(draftsRef.current)
    }
  }, [])

  const activateWorkspace = useCallback(
    (workspace) => {
      const targetId = workspace.workspace_id

      /*
       * 중요:
       * workspace를 떠날 때 현재 React state를 다시 session에 저장하지 않는다.
       *
       * 완료된 결과는 runBuild 성공 시 이미
       * workspaceSessionRef.current[id]에 정확하게 저장된다.
       *
       * 여기서 다시 mode/messages/result를 저장하면
       * stale closure가 정상 결과를 input/null 값으로 덮어쓸 수 있다.
       */

      selectedRef.current = targetId
      setSelectedId(targetId)

      setUploadReports([])
      setIssue(null)

      // 이전에 생성한 결과가 있으면 즉시 복원.
      // 없으면 restoreWorkspaceSession 내부에서 input 화면으로 초기화.
      restoreWorkspaceSession(targetId)

      rememberWorkspace(workspace)

      try {
        localStorage.setItem(
          SELECTED_WORKSPACE_KEY,
          targetId,
        )
      } catch {
        // Browser storage 사용 불가 시 selection persistence만 포기한다.
      }
    },
    [rememberWorkspace, restoreWorkspaceSession],
  )

  const editDraft = useCallback(
    (fields, id = selectedRef.current) => {
      if (!id) return

      draftsRef.current = {
        ...draftsRef.current,
        [id]: {
          ...draftsRef.current[id],
          ...fields,
        },
      }

      dirtyRef.current.add(id)

      setDrafts(draftsRef.current)

      setSaveStatus((previous) => ({
        ...previous,
        [id]: 'Unsaved changes',
      }))
    },
    [],
  )

  const saveDraft = useCallback((id) => {
    if (!id) {
      return Promise.resolve()
    }

    // workspace별 write를 순차 처리해서
    // 오래된 response가 최신 edit를 덮어쓰지 않도록 한다.
    const pending = (
      saveQueueRef.current[id] || Promise.resolve()
    )
      .catch(() => {})
      .then(async () => {
        if (!dirtyRef.current.has(id)) {
          return
        }

        const snapshot = {
          ...draftsRef.current[id],
        }

        if (!snapshot.name.trim()) {
          throw new Error(
            'Enter a workspace name before continuing.',
          )
        }

        setSaveStatus((previous) => ({
          ...previous,
          [id]: 'Saving…',
        }))

        const workspace = await workspaceRequest(
          `/workspaces/${id}`,
          jsonRequest('PATCH', snapshot),
        )

        setWorkspaces((previous) =>
          previous.map((item) =>
            item.workspace_id === id
              ? {
                  ...item,
                  name: workspace.name,
                  role: workspace.role,
                  task: workspace.task,
                  updated_at: workspace.updated_at,
                }
              : item,
          ),
        )

        if (
          JSON.stringify(draftsRef.current[id]) ===
          JSON.stringify(snapshot)
        ) {
          dirtyRef.current.delete(id)

          setSaveStatus((previous) => ({
            ...previous,
            [id]: 'Saved',
          }))
        }
      })
      .catch((error) => {
        setSaveStatus((previous) => ({
          ...previous,
          [id]: 'Not saved',
        }))

        throw error
      })

    saveQueueRef.current[id] = pending

    return pending
  }, [])

  const saveCurrentDraft = useCallback(async () => {
    const id = selectedRef.current

    try {
      await saveDraft(id)

      setIssue((previous) =>
        previous?.kind === 'save' &&
        previous.workspaceId === id
          ? null
          : previous,
      )
    } catch (error) {
      if (selectedRef.current === id) {
        setIssue({
          kind: 'save',
          workspaceId: id,
          title: 'Workspace changes were not saved',
          message: error.message,
        })
      }
    }
  }, [saveDraft])

  useEffect(() => {
    if (
      !selectedId ||
      !dirtyRef.current.has(selectedId) ||
      disabled
    ) {
      return
    }

    const timeout = setTimeout(
      saveCurrentDraft,
      700,
    )

    return () => clearTimeout(timeout)
  }, [
    drafts,
    selectedId,
    disabled,
    saveCurrentDraft,
  ])

  const loadWorkspaces = useCallback(async () => {
    if (actionLockRef.current) {
      return
    }

    actionLockRef.current = true

    setBusy('Loading workspaces')
    setIssue(null)

    try {
      const data = await workspaceRequest(
        '/workspaces',
      )

      setWorkspaces(data.workspaces)

      let rememberedId = ''

      try {
        rememberedId =
          localStorage.getItem(
            SELECTED_WORKSPACE_KEY,
          ) || ''
      } catch {
        // Optional storage.
      }

      const workspace =
        data.workspaces.find(
          (item) =>
            item.workspace_id === rememberedId,
        ) ||
        data.workspaces.find(
          (item) =>
            item.workspace_id === 'demo',
        ) ||
        data.workspaces[0]

      if (workspace) {
        activateWorkspace(workspace)
      }
    } catch (error) {
      setIssue({
        kind: 'load',
        title: 'Workspaces are unavailable',
        message: error.message,
      })
    } finally {
      actionLockRef.current = false
      setBusy('')
    }
  }, [activateWorkspace])

  useEffect(() => {
    loadWorkspaces()
  }, [loadWorkspaces])

  useEffect(() => {
    if (
      focusNameId === selectedId &&
      !disabled &&
      nameInputRef.current
    ) {
      nameInputRef.current.focus()
      nameInputRef.current.select()
    }
  }, [
    focusNameId,
    selectedId,
    disabled,
  ])

  async function selectWorkspace(
    id,
    withDemoTask = false,
  ) {
    if (
      actionLockRef.current ||
      (id === selectedRef.current &&
        !withDemoTask)
    ) {
      return
    }

    actionLockRef.current = true

    setBusy('Opening workspace')
    setIssue(null)

    try {
      await saveDraft(selectedRef.current)

      const workspace =
        await workspaceRequest(
          `/workspaces/${id}`,
        )

      activateWorkspace(workspace)

      setFocusNameId('')

      if (withDemoTask) {
        editDraft(
          {
            role: DEMO_ROLE,
            task: DEMO_TASK,
          },
          id,
        )

        await saveDraft(id)
      }
    } catch (error) {
      setIssue({
        title: 'Could not open workspace',
        message: error.message,
        retry: () =>
          selectWorkspace(
            id,
            withDemoTask,
          ),
      })
    } finally {
      actionLockRef.current = false
      setBusy('')
    }
  }

  async function createWorkspace() {
    if (actionLockRef.current) {
      return
    }

    actionLockRef.current = true

    setBusy('Creating workspace')
    setIssue(null)

    try {
      await saveDraft(
        selectedRef.current,
      )

      const workspace =
        await workspaceRequest(
          '/workspaces',
          jsonRequest('POST', {
            name: 'Untitled Workspace',
          }),
        )

      activateWorkspace(workspace)

      setFocusNameId(
        workspace.workspace_id,
      )
    } catch (error) {
      setIssue({
        title: 'Could not create workspace',
        message: error.message,
        retry: createWorkspace,
      })
    } finally {
      actionLockRef.current = false
      setBusy('')
    }
  }

  async function uploadFiles(files) {
    const id = selectedRef.current

    if (
      !files.length ||
      !id ||
      actionLockRef.current
    ) {
      return
    }

    actionLockRef.current = true

    setBusy('Adding sources')
    setIssue(null)

    const reports = []

    setUploadReports([])

    for (const file of files) {
      setBusy(`Adding ${file.name}`)

      try {
        if (
          !/\.(pdf|docx|txt|md|json)$/i.test(
            file.name,
          )
        ) {
          throw new Error(
            'Supported files: PDF, DOCX, TXT, MD and JSON.',
          )
        }

        if (!file.size) {
          throw new Error(
            'This file is empty.',
          )
        }

        if (
          file.size > MAX_FILE_BYTES
        ) {
          throw new Error(
            'File exceeds the 10 MiB limit.',
          )
        }

        const body = new FormData()

        body.append('file', file)

        const data =
          await workspaceRequest(
            `/workspaces/${id}/sources`,
            {
              method: 'POST',
              body,
            },
          )

        setWorkspaces((previous) =>
          previous.map((item) =>
            item.workspace_id === id
              ? {
                  ...item,
                  sources: [
                    ...item.sources,
                    data.source,
                  ],
                }
              : item,
          ),
        )

        /*
         * source corpus가 바뀌었으므로
         * 기존 ContextPack result는 stale이다.
         */
        invalidateWorkspaceSession(id)
        clearResult()

        reports.push({
          filename: file.name,
          status: 'success',
          warnings:
            data.warnings || [],
        })
      } catch (error) {
        reports.push({
          filename: file.name,
          status: 'error',
          message: error.message,
          file,
        })
      }

      setUploadReports([
        ...reports,
      ])
    }

    actionLockRef.current = false
    setBusy('')
  }

  async function removeSource(source) {
    const id = selectedRef.current

    if (
      actionLockRef.current ||
      source.source_type !== 'upload'
    ) {
      return
    }

    actionLockRef.current = true

    setBusy(
      `Removing ${sourceLabel(source)}`,
    )

    setIssue(null)

    try {
      await workspaceRequest(
        `/workspaces/${id}/sources/${source.id}`,
        {
          method: 'DELETE',
        },
      )

      setWorkspaces((previous) =>
        previous.map((item) =>
          item.workspace_id === id
            ? {
                ...item,
                sources:
                  item.sources.filter(
                    (candidate) =>
                      candidate.id !==
                      source.id,
                  ),
              }
            : item,
        ),
      )

      /*
       * source corpus 변경 -> 기존 결과 무효화
       */
      invalidateWorkspaceSession(id)

      setUploadReports([])
      clearResult()
    } catch (error) {
      setIssue({
        title: 'Could not remove source',
        message: error.message,
        retry: () =>
          removeSource(source),
      })
    } finally {
      actionLockRef.current = false
      setBusy('')
    }
  }

  async function runBuild() {
    const id = selectedRef.current
    const form =
      draftsRef.current[id]

    if (
      actionLockRef.current ||
      !id ||
      !sources.length ||
      !form?.role.trim() ||
      !form?.task.trim()
    ) {
      return
    }

    actionLockRef.current = true

    /*
     * 새 분석을 시작하므로 해당 workspace의
     * 이전 결과 session은 제거한다.
     */
    invalidateWorkspaceSession(id)

    clearResult()

    setIssue(null)
    setMode('processing')

    const started = Date.now()

    timerRef.current = setInterval(
      () =>
        setElapsed(
          Math.floor(
            (Date.now() - started) /
              1000,
          ),
        ),
      250,
    )

    /*
     * React setState는 비동기이므로
     * 나중에 `messages` state를 다시 읽지 않는다.
     * 현재 요청의 user message를 로컬 변수로 고정한다.
     */
    const userMessage = {
      role: 'user',
      text: `${form.role.trim()}\n\n${form.task.trim()}`,
    }

    setMessages([userMessage])

    try {
      await saveDraft(id)

      const data =
        await workspaceRequest(
          '/analyze',
          jsonRequest('POST', {
            workspace_id: id,
            role: form.role.trim(),
            task: form.task.trim(),
          }),
        )

      if (
        typeof data.handoff !==
          'string' ||
        !data.handoff.trim()
      ) {
        throw new Error(
          'The API did not return a Handoff Context.',
        )
      }

      if (
        data.workspace_id &&
        data.workspace_id !== id
      ) {
        throw new Error(
          'The returned context belongs to a different workspace. Please retry.',
        )
      }

      const durationSec =
        typeof data.duration_sec ===
        'number'
          ? data.duration_sec
          : Math.floor(
              (Date.now() - started) /
                1000,
            )

      const assistantMessage = {
        role: 'assistant',
        status: 'done',
        elapsedSec: durationSec,
        toolCalls:
          data.trace
            ?.mcp_tool_calls,
        skillUsed:
          data.trace?.skill_used,
        handoff: data.handoff,
        trace: data.trace || {},
      }

      /*
       * stale React state를 사용하지 않고
       * 이번 요청에 해당하는 message 배열을 직접 만든다.
       */
      const nextMessages = [
        userMessage,
        assistantMessage,
      ]

      /*
       * workspace별 완료 결과를 메모리에 저장한다.
       *
       * Alpha / Beta 이동 시 이 값만 복원하며
       * /analyze를 다시 호출하지 않는다.
       */
      workspaceSessionRef.current[
        id
      ] = {
        mode: 'result',
        messages: nextMessages,
        result: data,
        elapsed: durationSec,
        errorDetail: '',
        inspectorVisible: true,
      }

      /*
       * 요청 도중 다른 workspace가 선택된 경우
       * 결과는 해당 workspace session에만 저장하고
       * 현재 화면을 덮어쓰지 않는다.
       */
      if (
        selectedRef.current === id
      ) {
        setMessages(nextMessages)
        setResult(data)
        setElapsed(durationSec)
        setMode('result')
        setErrorDetail('')
        setInspectorVisible(true)
      }
    } catch (error) {
      /*
       * 현재 보고 있는 workspace의 요청일 때만
       * error 화면을 표시한다.
       */
      if (
        selectedRef.current === id
      ) {
        setErrorDetail(
          error.message,
        )

        setMode('error')
      }
    } finally {
      clearInterval(
        timerRef.current,
      )

      actionLockRef.current = false
    }
  }

  async function copyHandoff() {
    if (!result) {
      return
    }

    try {
      await navigator.clipboard.writeText(
        result.handoff,
      )

      setCopyError('')
      setCopied(true)

      clearTimeout(
        copyTimerRef.current,
      )

      copyTimerRef.current =
        setTimeout(
          () => setCopied(false),
          2000,
        )
    } catch (error) {
      setCopied(false)

      setCopyError(
        `Could not copy the handoff. ${
          error.message ||
          'Clipboard access is unavailable.'
        }`,
      )
    }
  }

  useEffect(() => {
    if (!messages.length) {
      return
    }

    const frame =
      requestAnimationFrame(() => {
        messageListRef.current?.scrollTo(
          {
            top:
              messageListRef.current
                .scrollHeight,
            behavior: 'smooth',
          },
        )
      })

    return () =>
      cancelAnimationFrame(frame)
  }, [messages, mode])

  useEffect(
    () => () => {
      clearInterval(
        timerRef.current,
      )

      clearTimeout(
        copyTimerRef.current,
      )
    },
    [],
  )

  const retryIssue =
    issue?.kind === 'load'
      ? loadWorkspaces
      : issue?.kind === 'save'
        ? saveCurrentDraft
        : issue?.retry

  const helperText = !selected
    ? 'Connect to the API to create or select a workspace.'
    : !sources.length
      ? 'Add at least one source file before building context.'
      : 'Ctrl+Enter to build · Context is gathered only from this workspace’s sources.'

  return (
    <>
      <div className="aurora-layer">
        <Aurora
          colorStops={[
            '#071A3D',
            '#2563EB',
            '#7C3AED',
          ]}
          blend={0.35}
          amplitude={0.55}
          speed={0.35}
        />
      </div>

      <div className="app-shell">
        <Sidebar
          workspaces={workspaces.map(
            (item) => ({
              ...item,
              name:
                drafts[
                  item.workspace_id
                ]?.name || item.name,
            }),
          )}
          selectedId={selectedId}
          sources={sources}
          onNewPack={
            createWorkspace
          }
          onSelectWorkspace={
            selectWorkspace
          }
          onAddSources={() =>
            fileInputRef.current?.click()
          }
          onRemoveSource={
            removeSource
          }
          disabled={disabled}
          hasWorkspace={Boolean(
            selected,
          )}
          status={
            mode === 'processing'
              ? 'Working'
              : busy
                ? 'Loading'
                : !selected
                  ? 'Unavailable'
                  : 'Ready'
          }
        />

        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPTED_FILES}
          multiple
          className="sr-only"
          aria-label="Upload source files"
          tabIndex={-1}
          disabled={
            disabled || !selected
          }
          onChange={(event) => {
            const files =
              Array.from(
                event.target.files ||
                  [],
              )

            event.target.value = ''

            uploadFiles(files)
          }}
        />

        <header className="page-topbar">
          <div className="page-topbar-left">
            <span
              className="page-topbar-title"
              title={
                draft.name ||
                'ContextPack'
              }
            >
              {draft.name ||
                'ContextPack'}
            </span>

            {mode ===
              'processing' && (
              <>
                <span className="page-topbar-stat">
                  Working
                </span>

                <span className="page-topbar-stat-value">
                  {formatElapsed(
                    elapsed,
                  )}
                </span>
              </>
            )}

            {mode === 'result' &&
              result && (
                <>
                  <span className="page-topbar-stat">
                    Duration
                  </span>

                  <span className="page-topbar-stat-value">
                    {typeof result.duration_sec ===
                    'number'
                      ? `${result.duration_sec.toFixed(
                          1,
                        )}s`
                      : '—'}
                  </span>
                </>
              )}
          </div>

          <div
            className="page-topbar-right"
            title={sourceSummary}
          >
            <span className="page-topbar-stat">
              Sources{' '}
              {sources.length}
            </span>

            <span className="page-topbar-stat-value source-summary">
              {sourceSummary}
            </span>
          </div>
        </header>

        <main className="workspace-area">
          <div className="workspace-main">
            <div
              className="workspace-content"
              ref={messageListRef}
            >
              {selected && (
                <div className="workspace-details">
                  <div className="workspace-details-label">
                    <label htmlFor="workspace-name">
                      {selected.is_demo
                        ? 'Demo workspace'
                        : 'Workspace name'}
                    </label>

                    <span
                      className="workspace-save-status"
                      role="status"
                    >
                      {saveStatus[
                        selectedId
                      ] || 'Saved'}
                    </span>
                  </div>

                  <input
                    id="workspace-name"
                    ref={
                      nameInputRef
                    }
                    className="workspace-name-input"
                    value={draft.name}
                    maxLength={120}
                    readOnly={
                      selected.is_demo
                    }
                    disabled={
                      disabled
                    }
                    onChange={(
                      event,
                    ) =>
                      editDraft({
                        name:
                          event
                            .target
                            .value,
                      })
                    }
                    onBlur={() => {
                      setFocusNameId(
                        '',
                      )

                      saveCurrentDraft()
                    }}
                    onKeyDown={(
                      event,
                    ) => {
                      if (
                        event.key ===
                        'Enter'
                      ) {
                        event.currentTarget.blur()
                      }
                    }}
                  />

                  <p className="workspace-description">
                    {selected.is_demo
                      ? 'Partial Refund fixtures are demo data. You can also add your own files.'
                      : 'Add your source files, then describe your role and current task.'}
                  </p>
                </div>
              )}

              {busy && (
                <div
                  className="workspace-activity"
                  role="status"
                >
                  {busy}
                </div>
              )}

              {issue && (
                <div
                  className="workspace-notice workspace-notice--error"
                  role="alert"
                >
                  <strong>
                    {issue.title}
                  </strong>

                  <p>
                    {issue.message}
                  </p>

                  {retryIssue && (
                    <button
                      type="button"
                      className="load-demo-btn"
                      disabled={
                        disabled
                      }
                      onClick={
                        retryIssue
                      }
                    >
                      Try Again
                    </button>
                  )}
                </div>
              )}

              {uploadReports.length >
                0 && (
                <div
                  className="upload-reports"
                  aria-live="polite"
                >
                  {uploadReports.map(
                    (
                      report,
                      index,
                    ) => (
                      <div
                        key={`${report.filename}-${index}`}
                        className={`upload-report upload-report--${report.status}`}
                      >
                        <div>
                          <strong>
                            {
                              report.filename
                            }
                          </strong>

                          <span>
                            {report.status ===
                            'success'
                              ? 'Added'
                              : 'Not added'}
                          </span>
                        </div>

                        {report.message && (
                          <p>
                            {
                              report.message
                            }
                          </p>
                        )}

                        {report.warnings?.map(
                          (
                            warning,
                            i,
                          ) => (
                            <p
                              key={
                                i
                              }
                              className="upload-warning"
                            >
                              {warning.message ||
                                String(
                                  warning,
                                )}
                            </p>
                          ),
                        )}

                        {report.file && (
                          <button
                            type="button"
                            className="upload-retry"
                            disabled={
                              disabled
                            }
                            onClick={() =>
                              uploadFiles(
                                [
                                  report.file,
                                ],
                              )
                            }
                          >
                            Retry this
                            file
                          </button>
                        )}
                      </div>
                    ),
                  )}
                </div>
              )}

              {copyError && (
                <div
                  className="workspace-notice workspace-notice--error"
                  role="alert"
                >
                  <p>
                    {copyError}
                  </p>

                  <button
                    type="button"
                    className="load-demo-btn"
                    onClick={
                      copyHandoff
                    }
                  >
                    Try Again
                  </button>
                </div>
              )}

              <AnimatePresence>
                {mode ===
                  'input' && (
                  <motion.div
                    key="input"
                    className="message-list"
                    initial={{
                      opacity: 0,
                      y: 8,
                    }}
                    animate={{
                      opacity: 1,
                      y: 0,
                    }}
                    exit={{
                      opacity: 0,
                    }}
                    transition={{
                      duration: 0.2,
                    }}
                  >
                    <div className="empty-header">
                      <div className="agent-identity">
                        <span
                          className="agent-dot"
                          aria-hidden="true"
                        />

                        <span className="agent-name">
                          ContextPack
                          Agent
                        </span>

                        <span className="agent-meta">
                          Powered by
                          Solar Pro 4 ·
                          Hermes
                        </span>
                      </div>

                      <h2 className="empty-title">
                        What are you
                        working on?
                      </h2>

                      <p className="empty-sub">
                        Give
                        ContextPack your
                        role and current
                        task.
                        <br />
                        It will gather
                        only the context
                        the next agent
                        needs.
                      </p>

                      <div
                        className="empty-sources"
                        aria-label="Selected workspace sources"
                      >
                        {sources.map(
                          (source) => (
                            <span
                              key={
                                source.id
                              }
                              className="source-chip"
                              title={sourceLabel(
                                source,
                              )}
                            >
                              {sourceLabel(
                                source,
                              )}

                              {source.source_type ===
                              'demo'
                                ? ' · Demo'
                                : ''}
                            </span>
                          ),
                        )}

                        {selected &&
                          !sources.length && (
                            <button
                              type="button"
                              className="load-demo-btn"
                              disabled={
                                disabled
                              }
                              onClick={() =>
                                fileInputRef.current?.click()
                              }
                            >
                              + Add
                              Sources
                            </button>
                          )}
                      </div>
                    </div>
                  </motion.div>
                )}

                {messages.map(
                  (
                    message,
                    index,
                  ) => (
                    <motion.div
                      key={index}
                      initial={{
                        opacity: 0,
                        y: 8,
                      }}
                      animate={{
                        opacity: 1,
                        y: 0,
                      }}
                      transition={{
                        duration:
                          0.25,
                      }}
                    >
                      {message.role ===
                      'user' ? (
                        <MessageBubble
                          text={
                            message.text
                          }
                        />
                      ) : (
                        <AssistantCard
                          message={
                            message
                          }
                        />
                      )}
                    </motion.div>
                  ),
                )}

                {mode ===
                  'processing' && (
                  <motion.div
                    key="processing"
                    initial={{
                      opacity: 0,
                    }}
                    animate={{
                      opacity: 1,
                    }}
                  >
                    <AssistantCard
                      message={{
                        status:
                          'loading',
                        elapsedSec:
                          elapsed,
                        sources,
                      }}
                    />
                  </motion.div>
                )}

                {mode ===
                  'error' && (
                  <motion.div
                    key="error"
                    initial={{
                      opacity: 0,
                    }}
                    animate={{
                      opacity: 1,
                    }}
                  >
                    <AssistantCard
                      message={{
                        status:
                          'error',
                        errorDetail,
                      }}
                      onRetry={
                        runBuild
                      }
                    />
                  </motion.div>
                )}
              </AnimatePresence>

              <div
                className={`agent-composer ${
                  mode ===
                  'processing'
                    ? 'composer--working'
                    : ''
                }`}
              >
                <AgentComposer
                  role={draft.role}
                  task={draft.task}
                  onRoleChange={(
                    role,
                  ) =>
                    editDraft({
                      role,
                    })
                  }
                  onTaskChange={(
                    task,
                  ) =>
                    editDraft({
                      task,
                    })
                  }
                  onBlur={
                    saveCurrentDraft
                  }
                  onBuild={
                    runBuild
                  }
                  onLoadDemo={() =>
                    selectWorkspace(
                      'demo',
                      true,
                    )
                  }
                  disabled={
                    disabled ||
                    !selected
                  }
                  demoDisabled={
                    disabled
                  }
                  isProcessing={
                    mode ===
                    'processing'
                  }
                  buildDisabled={
                    !sources.length
                  }
                  helperText={
                    helperText
                  }
                  showRole={
                    mode !==
                    'processing'
                  }
                  showTask={
                    mode !==
                    'processing'
                  }
                />
              </div>
            </div>
          </div>

          {mode === 'result' &&
            result &&
            inspectorVisible && (
              <motion.div
                className="inspector-panel"
                key="inspector"
                initial={{
                  opacity: 0,
                  x: 12,
                }}
                animate={{
                  opacity: 1,
                  x: 0,
                }}
                exit={{
                  opacity: 0,
                }}
                transition={{
                  duration: 0.3,
                  ease: [
                    0.2,
                    0,
                    0,
                    1,
                  ],
                }}
              >
                <ContextInspector
                  handoff={
                    result.handoff
                  }
                  onCopy={
                    copyHandoff
                  }
                  copied={copied}
                  mode={mode}
                />
              </motion.div>
            )}
        </main>
      </div>
    </>
  )
}