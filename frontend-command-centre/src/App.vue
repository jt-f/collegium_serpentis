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
          <ServerConnectionMonitor />
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
import ServerConnectionMonitor from './components/ServerConnectionMonitor.vue'
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
@import url('https://fonts.googleapis.com/css2?family=Roboto+Condensed:wght@400;700&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Special+Elite&display=swap');

#app-container {
  display: flex;
  flex-direction: column;
  height: 100vh;
  font-family: 'Roboto Condensed', sans-serif;
  background-color: #1A1A1A; /* Base Background */
  color: #EAEAEA; /* Primary Text Color */
}

.app-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 1.5rem;
  background-color: var(--color-background-soft); /* Slightly Lighter Background */
  border-bottom: 1px solid var(--color-border); /* Olive Drab/Spy Green */
  box-shadow: 0 2px 5px rgba(0, 0, 0, 0.3); /* Added depth */
}

.app-header h1 {
  margin: 0;
  font-family: 'Special Elite', cursive;
  font-size: 2rem; 
  font-weight: normal; 
  color: var(--color-heading);
  text-shadow: 1px 1px 3px rgba(0, 0, 0, 0.6); /* Added subtle text shadow */
}

.app-main {
  flex-grow: 1;
  padding: 1.5rem;
  overflow-y: auto;
  /* Subtle gradient for depth */
  background: radial-gradient(ellipse at center, 
                              var(--color-background-soft) 0%, 
                              var(--color-background) 70%); 
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
  flex: 1.618; /* Approximate Golden Ratio */
}

.app-footer {
  padding: 0.75rem 1.5rem;
  background-color: #2C2F33; /* Slightly Lighter Background */
  color: #A9A9A9; /* Lighter Muted Gray for footer text */
  text-align: center;
  font-size: 0.85em;
  border-top: 1px solid var(--color-border); /* Olive Drab/Spy Green */
  box-shadow: 0 -2px 5px rgba(0, 0, 0, 0.3); /* Added depth */
}
</style>
