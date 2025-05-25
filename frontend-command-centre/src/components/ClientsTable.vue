<template>
  <div class="clients-table-section">
    <h2>Connected Clients ({{ connectedCount }})</h2>
    <div v-if="!isConnected" class="connection-warning">
      <p>⚠️ WebSocket connection: {{ connectionStatus }}</p>
      <button v-if="connectionStatus === 'error'" @click="retryConnection" class="retry-btn">
        🔄 Retry Connection
      </button>
    </div>

    <div class="table-scroll-wrapper">
      <table class="clients-table-content">
        <thead>
          <tr>
            <th style="width: 60px;">Avatar</th>
            <th style="width: 20%;">Client ID</th>
            <th style="width: 80px;">Status</th>
            <th style="width: 10%;">Type</th>
            <th style="width: 100px;">CPU</th>
            <th style="width: 100px;">RAM</th>
            <th style="width: 15%;">Last Seen</th>
            <th style="width: 80px;">Details</th>
            <th style="min-width: 150px;">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="clients.length === 0 && isConnected">
            <td colspan="9" class="no-clients-message">
              No clients connected or reporting.
            </td>
          </tr>
          <tr v-if="!isConnected && connectionStatus !== 'error'">
             <td colspan="9" class="no-clients-message">
              {{ getNoClientsMessage() }}
            </td>
          </tr>
          <ClientTableRow
            v-for="client in clients"
            :key="client.id"
            :client-data="client"
            @action="handleClientAction"
          />
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ClientTableRow from './ClientTableRow.vue'
import { useWebSocketStore } from '../stores/websocket.js'

const wsStore = useWebSocketStore()

const clients = computed(() => wsStore.clients)
const connectedCount = computed(() => wsStore.connectedClientsCount)
const isConnected = computed(() => wsStore.isConnected)
const connectionStatus = computed(() => wsStore.connectionStatus)

function getNoClientsMessage() {
  switch (connectionStatus.value) {
    case 'connecting':
      return 'Connecting to server...'
    case 'error':
      return 'Cannot connect to server. Please check if the backend is running and accessible.'
    case 'connected':
      return 'No clients connected or reporting.'
    default:
      return 'Attempting to connect or server unavailable.'
  }
}

async function retryConnection() {
  try {
    await wsStore.initialize()
  } catch (error) {
    console.error('Failed to retry connection:', error)
  }
}

async function handleClientAction({ action, clientId }) {
  try {
    let result
    switch (action) {
      case 'pause':
        result = await wsStore.pauseClient(clientId)
        console.log(`Paused client ${clientId}:`, result)
        break
      case 'resume':
        result = await wsStore.resumeClient(clientId)
        console.log(`Resumed client ${clientId}:`, result)
        break
      case 'disconnect':
        result = await wsStore.disconnectClient(clientId)
        console.log(`Disconnected client ${clientId}:`, result)
        break
      default:
        console.warn(`Unknown action: ${action}`)
    }
  } catch (error) {
    console.error(`Failed to ${action} client ${clientId}:`, error)
    // You could add a toast notification here in the future
  }
}
</script>

<style scoped>
.clients-table-section {
  margin: 2rem auto 0 auto;
  padding: 1rem 1.5rem 2rem 1.5rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background-color: var(--color-background-soft);
  width: 100%;
  max-width: 1600px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  flex-grow: 1;
  flex-shrink: 1;
  min-height: 0;
  overflow: hidden;
}

.clients-table-section h2 {
  margin-top: 0;
  margin-bottom: 1rem;
  font-size: 1.4rem;
  font-weight: 700;
  color: var(--color-heading);
  border-bottom: 2px solid var(--color-text-muted);
  padding-bottom: 0.5rem;
  flex-shrink: 0;
}

.connection-warning {
  background-color: var(--color-accent-red);
  border: 1px solid var(--color-text-on-manila);
  border-radius: 4px;
  padding: 0.75rem;
  margin-bottom: 1rem;
  flex-shrink: 0;
}

.connection-warning p {
  margin: 0;
  color: var(--color-text-on-accent);
  font-weight: 500;
}

.retry-btn {
  background-color: var(--color-background-mute);
  color: var(--color-text);
  border: 1px solid var(--color-border);
  padding: 0.5rem 1rem;
  border-radius: 4px;
  cursor: pointer;
  margin-top: 0.5rem;
  transition: background-color 0.3s ease, border-color 0.3s ease;
}

.retry-btn:hover {
  background-color: var(--color-border);
  border-color: var(--color-border-hover);
}

.table-scroll-wrapper {
  max-height: 450px;
  overflow-y: auto;
  border: 1px solid #666666;
  border-top: none;
  flex-grow: 1;
  min-height: 0;
}

.clients-table-content {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}

.clients-table-content th,
.clients-table-content td {
  padding: 0.6rem 0.75rem;
  text-align: left;
  border-left: 1px solid #666666;
  border-bottom: 1px solid #666666;
  font-family: 'Roboto Condensed', sans-serif;
  font-size: 0.9rem;
  word-wrap: break-word;
}

.clients-table-content th:first-child,
.clients-table-content td:first-child {
  border-left: none;
}

.clients-table-content th {
  background-color: var(--color-background-mute);
  font-weight: 700;
  color: var(--color-heading);
  position: sticky;
  top: 0;
  z-index: 10;
  border-top: 1px solid #666666;
}

.clients-table-content tbody td {
  background-color: var(--color-background);
  color: var(--color-text);
}

.clients-table-content tbody tr:hover td {
  background-color: var(--color-background-soft);
}

.no-clients-message {
  text-align: center;
  color: var(--color-text-muted);
  padding: 1.5rem;
  font-style: italic;
}
</style>
