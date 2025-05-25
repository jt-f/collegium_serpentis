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
      </div>
      <ClientsTable />
    </main>
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
  overflow: hidden;
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
  padding: 2rem 1.5rem 1rem 1.5rem;
  background: radial-gradient(ellipse at center, 
                              var(--color-background-soft) 0%, 
                              var(--color-background) 70%);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.main-layout {
  display: flex;
  gap: 2.5rem;
  align-items: flex-start;
  width: 100%;
  flex-shrink: 0;
}

.left-pane {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2rem;
  min-width: 270px;
}

@media (max-width: 900px) {
  .main-layout {
    flex-direction: column;
    gap: 1.5rem;
  }
  .left-pane {
    min-width: 0;
    width: 100%;
  }
  .app-main {
    padding: 1rem 0.5rem 1rem 0.5rem;
  }
}
</style>
