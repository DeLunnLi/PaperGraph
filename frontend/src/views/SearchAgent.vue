<template>
  <div class="search-agent-page" :class="{ 'has-results': hasSearched, 'show-sidebar': showSidebar }">
    <div v-if="!hasSearched" class="centered-search">
      <div v-if="conversations.length > 0" class="initial-history-btn" @click="showSidebar = true">
        <MenuOutlined />
        <span>历史 ({{ conversations.length }})</span>
      </div>
      <div class="search-hero">
        <div class="logo-large">
          <div class="logo-large__mark"><RobotOutlined /></div>
          <div class="logo-large__text">
            <span class="logo-large__title">文献助手</span>
            <span class="logo-large__subtitle">语义检索 · 多源聚合 · 智能推荐</span>
          </div>
        </div>
        <div class="search-box">
          <a-textarea
            v-model:value="userInput"
            :rows="3"
            placeholder="输入关键词、研究方向或论文标题，即可检索相关论文"
            :disabled="isLoading"
            class="search-input"
            @compositionstart="onTextareaCompositionStart"
            @compositionend="onTextareaCompositionEnd"
            @keydown="handleKeydown"
          />
          <div class="search-actions">
            <div class="search-actions__left">
              <a-tooltip title="分解子问题、多轮迭代检索、RRF融合，更全更准但更慢">
                <div class="deep-search-toggle">
                  <a-switch v-model:checked="deepSearch" size="small" />
                  <span class="deep-search-toggle__label">深度搜索</span>
                </div>
              </a-tooltip>
            </div>
            <a-button
              type="primary"
              size="large"
              :loading="isLoading"
              :disabled="!userInput.trim()"
              @click="sendMessage"
              class="send-btn"
            >
              <SendOutlined v-if="!isLoading" /> 搜索
            </a-button>
          </div>
        </div>
        <div class="search-examples">
          <span class="search-examples__label">试试：</span>
          <button
            v-for="(ex, i) in examplePrompts"
            :key="i"
            type="button"
            class="search-example-chip"
            :disabled="isLoading"
            @click="useExample(ex)"
          >{{ ex }}</button>
        </div>
      </div>
    </div>
    <div v-else class="chat-interface">
      <div class="chat-header">
        <div class="header-left">
          <a-button type="text" size="small" class="sidebar-toggle-btn" aria-label="切换历史侧栏" title="历史对话" @click="showSidebar = !showSidebar">
            <MenuOutlined />
          </a-button>
          <RobotOutlined class="header-icon" />
          <span class="header-title">文献助手</span>
        </div>
        <div class="header-right">
          <a-space>
            <a-button size="small" @click="createNewConversation">
              <PlusOutlined /> 新对话
            </a-button>
          </a-space>
        </div>
      </div>
      <div class="messages-container" ref="messagesContainer">
        <div v-for="(msg, index) in messages" :key="index" class="message" :class="msg.role">
          <div v-if="msg.role === 'user'" class="user-bubble">
            <div class="message-content">{{ msg.content }}</div>
          </div>
          <div v-else class="ai-message">
            <div class="ai-avatar">
              <RobotOutlined />
            </div>
            <div class="ai-content">
              <SearchProgressTrace
                v-if="msg.searchSteps?.length || isLoading"
                :steps="msg.searchSteps || []"
                :sub-queries="msg.deepSubQueries"
                :loading="isLoading && index === messages.length - 1"
              />
              <div v-if="msg.content && (!isLoading || index !== messages.length - 1)" class="ai-text" v-html="renderMarkdownWithLatex(msg.content)"></div>
              <div v-else-if="isLoading && index === messages.length - 1 && !msg.searchSteps?.length" class="ai-text ai-text--loading">{{ msg.content }}</div>
              <SearchToolTrace v-if="msg.toolCalls?.length" :tool-calls="msg.toolCalls" />
              <div v-if="msg.searchParams" class="search-params">
                <a-tag v-if="msg.searchParams.venue" size="small" color="purple">{{ msg.searchParams.venue }}</a-tag>
              </div>
              <SearchResultPapers
                v-if="msg.results?.length"
                :papers="msg.results"
                :total="msg.total ?? msg.results.length"
                @save-one="saveOne"
                @save-all="saveAllFromMessage(msg)"
              />
              <div v-if="msg.results && msg.results.length === 0 && !isLoading && !msg.isError" class="no-results">
                <a-empty :image="Empty.PRESENTED_IMAGE_SIMPLE">
                  <template #description>
                    <p style="color: var(--pg-text-secondary); margin-bottom: 12px;">未找到相关论文，试试以下建议：</p>
                  </template>
                  <div class="no-results__suggestions">
                    <button v-for="s in searchSuggestions" :key="s" class="no-results__chip" @click="userInput = s; sendMessage()">
                      {{ s }}
                    </button>
                  </div>
                </a-empty>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div class="chat-input-area">
        <div class="input-wrapper">
          <a-textarea
            v-model:value="userInput"
            :rows="2"
            :disabled="isLoading"
            class="chat-textarea"
            placeholder="输入关键词或研究方向…"
            @compositionstart="onTextareaCompositionStart"
            @compositionend="onTextareaCompositionEnd"
            @keydown="handleKeydown"
          />
          <a-tooltip title="深度搜索：分解子问题、多轮检索、RRF融合">
            <a-switch v-model:checked="deepSearch" size="small" class="chat-deep-toggle" />
          </a-tooltip>
          <a-button
            type="primary"
            class="chat-send-btn"
            :loading="isLoading"
            :disabled="!userInput.trim() || isLoading"
            @click="sendMessage"
          >
            <SendOutlined v-if="!isLoading" class="send-icon" />
          </a-button>
        </div>
        <div class="input-footer">
          <span class="hint">按 Enter 发送，Shift+Enter 换行</span>
          <span v-if="deepSearch" class="hint hint--deep">深度搜索已开启</span>
        </div>
      </div>
      <div v-if="conversations.length > 0 && !showSidebar" class="sidebar-toggle right-toggle" @click="showSidebar = true">
        <MenuOutlined />
        <span>历史</span>
      </div>
    </div>
    <div v-if="conversations.length > 0" class="conversations-sidebar right-sidebar" :class="{ 'collapsed': !showSidebar }">
      <div class="sidebar-header">
        <span class="sidebar-title">历史对话</span>
        <a-button type="text" size="small" @click="showSidebar = false" class="close-sidebar-btn">
          <CloseOutlined />
        </a-button>
      </div>
      <div class="sidebar-content">
        <a-button type="dashed" block size="small" @click="createNewConversation" class="new-chat-btn-sidebar">
          <PlusOutlined /> 新对话
        </a-button>
        <div class="conversations-list">
          <div
            v-for="conv in conversations"
            :key="conv.id"
            class="conversation-item"
            :class="{ active: currentConversationId === conv.id }"
            @click="loadConversation(conv.id)"
          >
            <div class="conversation-title">{{ conv.title || '未命名对话' }}</div>
            <div class="conversation-meta">
              <span class="conversation-time">{{ formatTime(conv.timestamp) }}</span>
              <span class="conversation-count">{{ conv.messageCount }}条消息</span>
            </div>
            <a-button
              type="text"
              size="small"
              class="delete-btn"
              @click.stop="deleteConversation(conv.id)"
            >
              <DeleteOutlined />
            </a-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { ref, nextTick, watch, onMounted, onActivated } from 'vue'
import { useRoute } from 'vue-router'
import { message, Empty, Modal } from 'ant-design-vue'
import {
  RobotOutlined,
  SendOutlined,
  SaveOutlined,
  PlusOutlined,
  MenuOutlined,
  CloseOutlined,
  DeleteOutlined,
} from '@ant-design/icons-vue'
import SearchResultPapers from '@/components/search/SearchResultPapers.vue'
import SearchToolTrace from '@/components/search/SearchToolTrace.vue'
import SearchProgressTrace from '@/components/search/SearchProgressTrace.vue'
import { savePapers } from '@/services/api'
import type { Paper } from '@/types'
import { renderMarkdownWithLatex } from '@/utils/markdown'
import { useSearchConversations } from '@/composables/useSearchConversations'
import { useSearchAgentChat } from '@/composables/useSearchAgentChat'
defineOptions({ name: 'SearchAgent' })
interface ToolCall {
  name: string
  status: 'running' | 'success' | 'error'
  params?: Record<string, any>
  result_summary?: string
}
interface Message {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  searchParams?: any
  toolCalls?: ToolCall[]
  results?: Paper[]
  total?: number
  isError?: boolean
  searchSteps?: any[]
  deepSubQueries?: string[]
}
const messages = ref<Message[]>([])
const userInput = ref('')
const textareaImeComposing = ref(false)
const isLoading = ref(false)
const hasSearched = ref(false)
const messagesContainer = ref<HTMLElement | null>(null)
const showSidebar = ref(false)
const deepSearch = ref(false)
const route = useRoute()
const examplePrompts = [
  '扩散模型在图像生成中的最新进展',
  '大模型推理能力评测综述',
  '图神经网络的可解释性方法',
  '检索增强生成 RAG 的优化策略',
]
const searchSuggestions = [
  'transformer attention mechanism survey',
  'large language model reasoning evaluation',
  'graph neural network explainability',
  'retrieval augmented generation RAG',
]
function useExample(text: string) {
  userInput.value = text
}
function generateTitle(msgs: Message[]): string {
  const firstUserMsg = msgs.find(m => m.role === 'user')
  if (firstUserMsg) {
    const title = firstUserMsg.content.slice(0, 30)
    return title.length >= 30 ? title + '...' : title
  }
  return '未命名对话'
}
const {
  conversations,
  currentConversationId,
  ensureCurrentConversationId,
  persistConversationAndState,
  loadConversation,
  createNewConversation,
  removeConversation,
  initFromStorage,
  formatTime,
} = useSearchConversations<Message>({
  messages,
  hasSearched,
  userInput,
  showSidebar,
  titleFromMessages: generateTitle,
})
function deleteConversation(id: string) {
  Modal.confirm({
    title: '删除对话',
    content: '确定要删除这条对话记录吗？删除后无法恢复。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => {
      removeConversation(id)
      message.success('已删除对话')
    },
  })
}
onMounted(() => {
  initFromStorage()
  scrollToBottom()
})
onActivated(() => {
  scrollToBottom()
})
watch(
  () => String(route.name || '').toLowerCase(),
  (name) => {
    if (name === 'search') scrollToBottom()
  },
)
watch([hasSearched, currentConversationId], () => {
  if (hasSearched.value) {
    persistConversationAndState()
  }
})
function onTextareaCompositionStart() {
  textareaImeComposing.value = true
}
function onTextareaCompositionEnd() {
  textareaImeComposing.value = false
}
function handleKeydown(e: KeyboardEvent) {
  if (e.key !== 'Enter' || e.shiftKey) return
  if (e.isComposing || textareaImeComposing.value) return
  e.preventDefault()
  sendMessage()
}
function scrollToBottom() {
  const apply = () => {
    const el = messagesContainer.value
    if (!el || !hasSearched.value) return
    el.scrollTop = el.scrollHeight
  }
  nextTick(() => {
    apply()
    requestAnimationFrame(() => {
      apply()
      requestAnimationFrame(apply)
    })
    window.setTimeout(apply, 60)
    window.setTimeout(apply, 220)
  })
}
const { sendMessage } = useSearchAgentChat({
  messages,
  userInput,
  isLoading,
  hasSearched,
  deepSearch,
  ensureCurrentConversationId,
  scrollToBottom,
  onConversationDirty: () => persistConversationAndState(),
})
const saveOne = async (paper: Paper) => {
  try {
    const res = await savePapers([paper])
    message.success(`已新增 ${res.added} 条到文献库`)
  } catch (e: unknown) {
    message.error((e as Error).message || '保存失败')
  }
}
const saveAllFromMessage = async (msg: Message) => {
  if (!msg.results || msg.results.length === 0) return
  try {
    const res = await savePapers(msg.results, { llm_classify: false })
    message.success(`已新增 ${res.added} 条到文献库`)
  } catch (e: unknown) {
    message.error((e as Error).message || '保存失败')
  }
}
watch(() => messages.value.length, scrollToBottom)
watch(currentConversationId, () => scrollToBottom())
</script>
<style scoped>
.search-agent-page {
  position: relative;
  flex: 1 1 0;
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: row;
  background: var(--pg-bg);
  overflow: hidden;
}
.search-agent-page.has-results {
  background: var(--pg-surface);
}
.conversations-sidebar {
  width: 280px;
  min-width: 280px;
  background: #f1f5f9;
  border-right: 1px solid var(--pg-border);
  display: flex;
  flex-direction: column;
  transition: all 0.3s ease;
}
.conversations-sidebar.right-sidebar {
  position: fixed;
  right: 0;
  top: 64px;
  bottom: 0;
  z-index: 1000;
  border-right: none;
  border-left: 1px solid var(--pg-border);
  box-shadow: var(--pg-shadow-lg);
}
.conversations-sidebar.collapsed {
  display: none;
}
.sidebar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  border-bottom: 1px solid var(--pg-border);
  background: white;
}
.sidebar-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--pg-text);
}
.close-sidebar-btn {
  color: var(--pg-text-secondary);
}
.sidebar-content {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
}
.new-chat-btn-sidebar {
  margin-bottom: 12px;
}
.conversations-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.conversation-item {
  padding: 12px;
  background: white;
  border-radius: 8px;
  border: 1px solid var(--pg-border);
  cursor: pointer;
  transition: all 0.2s ease;
  position: relative;
}
.conversation-item:hover {
  border-color: var(--pg-primary);
  box-shadow: 0 2px 8px rgba(59,130,246,0.1);
}
.conversation-item.active {
  border-color: var(--pg-primary);
  background: var(--pg-primary-soft);
}
.conversation-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--pg-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 4px;
  padding-right: 24px;
}
.conversation-meta {
  display: flex;
  gap: 8px;
  font-size: 11px;
  color: var(--pg-text-secondary);
}
.conversation-item .delete-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  opacity: 0;
  transition: opacity 0.2s ease;
  color: var(--pg-text-tertiary);
}
.conversation-item:hover .delete-btn {
  opacity: 1;
}
.conversation-item .delete-btn:hover {
  color: #ef4444;
}
.sidebar-toggle {
  position: fixed;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  background: white;
  border: 1px solid var(--pg-border);
  border-left: none;
  border-radius: 0 8px 8px 0;
  padding: 8px 12px 8px 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  font-size: 12px;
  color: var(--pg-text-secondary);
  z-index: 100;
  box-shadow: var(--pg-shadow-sm);
}
.sidebar-toggle:hover {
  color: var(--pg-primary);
  border-color: var(--pg-primary);
}
.sidebar-toggle.right-toggle {
  left: auto;
  right: 0;
  top: 80px;
  transform: none;
  border-right: none;
  border-radius: 8px 0 0 8px;
  padding: 8px 8px 8px 12px;
  box-shadow: var(--pg-shadow-sm);
  z-index: 200;
}
.search-agent-page.show-sidebar .sidebar-toggle.right-toggle {
  display: none;
}
.sidebar-toggle-btn {
  color: var(--pg-text-secondary);
  padding: 4px 8px;
}
.centered-search {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  background: var(--pg-bg);
  background-image: var(--pg-bg-aurora);
  overflow: hidden;
  z-index: 10;
}
.initial-history-btn {
  position: absolute;
  top: 20px;
  left: 20px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: white;
  border: 1px solid var(--pg-border);
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  color: var(--pg-text-secondary);
  z-index: 100;
  box-shadow: var(--pg-shadow-sm);
  transition: all 0.2s ease;
}
.initial-history-btn:hover {
  color: var(--pg-primary);
  border-color: var(--pg-primary);
  box-shadow: 0 4px 12px rgba(59,130,246,0.15);
}
.search-hero {
  width: 100%;
  max-width: min(680px, 90vw);
  text-align: center;
  animation: pg-hero-in 0.5s cubic-bezier(0.2, 0.8, 0.2, 1);
}
@keyframes pg-hero-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
.logo-large {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  margin-bottom: 18px;
}
.logo-large__mark {
  width: 52px;
  height: 52px;
  border-radius: 13px;
  background: var(--pg-surface);
  border: 1px solid var(--pg-border);
  color: var(--pg-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  box-shadow: var(--pg-shadow-sm);
}
.logo-large__text {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  line-height: 1.15;
}
.logo-large__title {
  font-family: var(--pg-font-serif);
  font-size: 32px;
  font-weight: 700;
  color: var(--pg-text-heading);
  letter-spacing: 0.01em;
}
.logo-large__subtitle {
  font-size: 13px;
  font-weight: 500;
  color: var(--pg-text-tertiary);
  letter-spacing: 0.04em;
  margin-top: 5px;
}
.search-box {
  background: var(--pg-surface);
  border-radius: var(--pg-radius-xl);
  box-shadow: var(--pg-shadow-md);
  border: 1px solid var(--pg-border);
  overflow: hidden;
  margin-bottom: 18px;
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}
.search-box:focus-within {
  border-color: #a5a8f5;
  box-shadow: var(--pg-shadow-lg), 0 0 0 4px rgba(99, 102, 241, 0.12);
}
.search-input {
  border: none;
  resize: none;
  padding: 20px 24px;
  font-size: 15px;
  line-height: 1.6;
  background: transparent;
}
.search-input:focus {
  box-shadow: none;
}
.search-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  border-top: 1px solid var(--pg-border-soft);
  background: var(--pg-bg-soft);
}
.search-actions__left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.deep-search-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}
.deep-search-toggle__label {
  font-size: 13px;
  color: var(--pg-text-secondary);
  user-select: none;
}
.search-examples {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  justify-content: center;
  max-width: 720px;
  margin: 0 auto;
}
.search-examples__label {
  font-size: 13px;
  color: var(--pg-text-tertiary);
}
.search-example-chip {
  padding: 5px 12px;
  font-size: 13px;
  color: var(--pg-text-secondary);
  background: var(--pg-surface);
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius-pill);
  cursor: pointer;
  transition: all 0.15s ease;
}
.search-example-chip:hover:not(:disabled) {
  color: var(--pg-primary-hover);
  border-color: #c7c9f5;
  background: var(--pg-primary-softer);
}
.search-example-chip:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.send-btn {
  height: 40px;
  padding: 0 26px;
  font-weight: 600;
  border-radius: var(--pg-radius-sm);
  background: var(--pg-primary);
  border: none;
}
.send-btn:hover:not(:disabled) {
  background: var(--pg-primary-hover) !important;
}
.chat-interface {
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100%;
  max-width: min(1000px, 100%);
  margin: 0 auto;
  width: 100%;
  overflow: hidden;
}
.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  border-bottom: 1px solid var(--pg-border);
  flex-shrink: 0;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.header-icon {
  font-size: 22px;
  color: var(--pg-primary);
}
.header-title {
  font-weight: 600;
  color: var(--pg-text);
  font-size: 16px;
}
.messages-container {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-gutter: stable;
  padding: 20px 0;
  min-height: 0;
}
.message {
  display: flex;
  padding: 16px 20px;
  gap: 12px;
}
.message.user {
  justify-content: flex-end;
}
.user-bubble {
  max-width: 80%;
  background: var(--pg-primary);
  color: var(--pg-text-inverse);
  padding: 12px 16px;
  border-radius: 16px 16px 4px 16px;
  font-size: 15px;
  line-height: 1.5;
}
.ai-message {
  display: flex;
  gap: 12px;
  width: 100%;
}
.ai-avatar {
  width: 32px;
  height: 32px;
  background: var(--pg-primary-soft);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--pg-primary);
  font-size: 18px;
  flex-shrink: 0;
}
.ai-content {
  flex: 1;
  min-width: 0;
  max-width: calc(100% - 50px);
}
.ai-text :deep(.katex) {
  font-size: 0.92em;
}
.ai-text {
  font-size: 15px;
  line-height: 1.7;
  color: var(--pg-text);
  margin-bottom: 12px;
}
.ai-text--loading {
  color: var(--pg-text-tertiary);
  font-style: italic;
}
.ai-text :deep(h4) {
  font-size: 13px;
  font-weight: 600;
  color: var(--pg-text-secondary);
  margin: 16px 0 8px;
  letter-spacing: 0.03em;
}
.ai-text :deep(h4:first-child) {
  margin-top: 4px;
}
.ai-text :deep(hr) {
  border: 0;
  border-top: 1px solid var(--pg-border);
  margin: 12px 0;
}
.ai-text :deep(ul) {
  margin: 6px 0 8px;
  padding-left: 1.1em;
}
.ai-text :deep(em) {
  color: var(--pg-text-secondary);
  font-size: 13px;
}
.search-params {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.chat-input-area {
  padding: 16px 20px 24px;
  border-top: 1px solid var(--pg-border);
  flex-shrink: 0;
}
.input-wrapper {
  display: flex;
  gap: 10px;
  align-items: center;
  background: var(--pg-surface);
  border-radius: var(--pg-radius-lg);
  padding: 8px 12px;
  border: 1px solid var(--pg-border);
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}
.input-wrapper:focus-within {
  border-color: #c7c9f5;
  box-shadow: 0 0 0 4px rgba(99, 102, 241, 0.1);
}
.chat-textarea {
  flex: 1;
  border: none;
  resize: none;
  background: transparent;
  padding: 4px;
  font-size: 15px;
}
.chat-textarea:focus {
  box-shadow: none;
}
.chat-deep-toggle {
  flex-shrink: 0;
}
.chat-send-btn {
  height: 36px;
  width: 36px;
  padding: 0;
  border-radius: 8px;
  background: var(--pg-primary);
  border: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.send-icon {
  font-size: 16px;
  line-height: 1;
}
.input-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 4px;
  margin-top: 8px;
  gap: 12px;
}
.hint {
  font-size: 12px;
  color: var(--pg-text-tertiary);
}
.hint--deep {
  color: var(--pg-primary);
  font-weight: 500;
}
.no-results {
  padding: 40px 0;
}
.no-results__suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  margin-top: 4px;
}
.no-results__chip {
  padding: 5px 14px;
  font-size: 13px;
  color: var(--pg-text-secondary);
  background: var(--pg-surface);
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius-pill);
  cursor: pointer;
  transition: all 0.15s ease;
}
.no-results__chip:hover {
  color: var(--pg-primary-hover);
  border-color: #c7c9f5;
  background: var(--pg-primary-softer);
}
.messages-container::-webkit-scrollbar {
  width: 6px;
}
.messages-container::-webkit-scrollbar-track {
  background: transparent;
}
.messages-container::-webkit-scrollbar-thumb {
  background: var(--pg-border);
  border-radius: 3px;
}
@media (max-width: 768px) {
  .search-hero {
  padding: 0 16px;
  }
  .logo-large__mark {
  width: 44px;
  height: 44px;
  font-size: 22px;
  border-radius: 12px;
  }
  .logo-large__title {
  font-size: 24px;
  }
  .logo-large__subtitle {
  font-size: 12px;
  }
  .search-box {
  margin-bottom: 16px;
  }
  .search-actions {
  flex-direction: column;
  gap: 12px;
  }
  .send-btn {
  width: 100%;
  }
  .chat-interface {
  max-width: 100%;
  }
  .message {
  padding: 12px 16px;
  }
  .user-bubble {
  max-width: 90%;
  }
  .conversations-sidebar {
  position: fixed;
  left: 0;
  top: 0;
  bottom: 0;
  z-index: 200;
  width: 280px;
  box-shadow: var(--pg-shadow-lg);
  }
  .conversations-sidebar.right-sidebar {
  left: auto;
  right: 0;
  box-shadow: var(--pg-shadow-lg);
  }
  .conversations-sidebar.collapsed {
  transform: translateX(-100%);
  display: flex;
  }
  .conversations-sidebar.right-sidebar.collapsed {
  transform: translateX(100%);
  }
  .current-conv-tag {
  max-width: 80px;
  }
  .sidebar-toggle.right-toggle {
  right: 0;
  left: auto;
  }
}
</style>