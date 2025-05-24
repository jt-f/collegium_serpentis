<template>
  <tr>
    <td class="avatar-cell">
      <div class="avatar-placeholder" :style="{ backgroundColor: avatarColor }"></div>
    </td>
    <td>{{ clientData.id }}</td>
    <td><span :class="statusClass">{{ clientData.status }}</span></td>
    <td>{{ clientData.lastSeen }}</td>
    <td>{{ clientData.details }}</td>
    <td class="actions-cell">
      <button @click="handleAction('pause')" :disabled="!clientData.canPause">Pause</button>
      <button @click="handleAction('resume')" :disabled="!clientData.canResume">Resume</button>
      <button @click="handleAction('disconnect')" :disabled="!clientData.canDisconnect">Disconnect</button>
    </td>
  </tr>
</template>

<script setup>
import { computed } from 'vue';
const props = defineProps({
  clientData: Object
});

// Placeholder - actual logic in next step
const avatarColor = computed(() => '#' + Math.floor(Math.random()*16777215).toString(16).padStart(6, '0'));
const statusClass = computed(() => 'status-' + (props.clientData.status || 'unknown').toLowerCase());

function handleAction(action) {
  console.log(`Action: ${action} on client ${props.clientData.id}`);
  // Actual emit or direct call logic in next step
}
</script>

<style scoped>
td {
  vertical-align: middle;
}
.avatar-placeholder {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background-color: #ccc; /* Placeholder */
  display: inline-block;
}
.actions-cell button {
  margin-right: 5px;
  padding: 0.3rem 0.6rem;
  font-size: 0.8em;
  border: 1px solid #ccc;
  background-color: #eee;
  cursor: pointer;
}
.actions-cell button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.status-connected { color: green; font-weight: bold; }
.status-disconnected { color: red; font-weight: bold; }
.status-paused { color: orange; font-weight: bold; }
.status-unknown { color: #777; }
</style>
