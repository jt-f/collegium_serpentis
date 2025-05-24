<template>
  <div id="app-container">
    <header class="app-header">
      <div class="header-left">
        <h1>Command Centre</h1>
      </div>
      <div class="header-right">
        <FrontendClientIdDisplay />
      </div>
    </header>
    <main class="app-main">
      <div class="main-layout">
        <div class="left-pane">
          <RedisStatus />
          <PlaceholderBlock title="Agent Conversations" content="Chat messages will appear here." />
          <PlaceholderBlock title="Agent Network Graph" content="Network graph will be displayed here." />
        </div>
        <div class="right-pane">
          <ClientsTable />
        </div>
      </div>
    </main>
    <footer class="app-footer">
      <p>&copy; 2024 Command Centre Interface</p>
    </footer>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted } from 'vue'
import FrontendClientIdDisplay from './components/FrontendClientIdDisplay.vue'
import RedisStatus from './components/RedisStatus.vue'
import ClientsTable from './components/ClientsTable.vue'
import PlaceholderBlock from './components/PlaceholderBlock.vue'
import { useWebSocketStore } from './stores/websocket.js'

const wsStore = useWebSocketStore()

onMounted(async () => {
  // Initialize WebSocket connection and data fetching
  try {
    await wsStore.initialize()
  } catch (error) {
    console.error('Failed to initialize WebSocket store:', error)
  }
})

onUnmounted(() => {
  // Clean up WebSocket connection
  wsStore.disconnect()
})
</script>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');

#app-container {
  display: flex;
  flex-direction: column;
  height: 100vh;
  font-family: 'Inter', sans-serif;
  background-color: #212529; /* Very Dark Gray */
  color: #F8F9FA; /* Light Gray/Off-White */
}

.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 1.5rem;
  background-color: #343A40; /* Dark Cool Gray */
  border-bottom: 1px solid #495057; /* Subtle Gray */
}

.app-header h1 {
  margin: 0;
  font-size: 1.6rem;
  font-weight: 600;
}

.app-main {
  flex-grow: 1;
  padding: 1.5rem;
  overflow-y: auto;
}

.main-layout {
  display: flex;
  gap: 1.5rem;
}

.left-pane {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.right-pane {
  flex: 2;
}

.app-footer {
  padding: 0.75rem 1.5rem;
  background-color: #343A40; /* Dark Cool Gray */
  color: #6C757D; /* Medium Gray */
  text-align: center;
  font-size: 0.8em;
  border-top: 1px solid #495057; /* Subtle Gray */
}
</style>
