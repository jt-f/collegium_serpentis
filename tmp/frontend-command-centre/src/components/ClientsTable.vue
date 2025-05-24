<template>
  <div class="status-block clients-table">
    <h2>Connected Clients</h2>
    <table>
      <thead>
        <tr>
          <th>Avatar</th>
          <th>Client ID</th>
          <th>Status</th>
          <th>Last Seen</th>
          <th>Details</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="clients.length === 0">
          <td colspan="6" class="no-clients-message">No clients connected or reporting.</td>
        </tr>
        <ClientTableRow v-for="client in clients" :key="client.id" :client-data="client" />
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import ClientTableRow from './ClientTableRow.vue';
// Placeholder - actual logic in next step
const clients = ref([]);
</script>

<style scoped>
.status-block {
  padding: 1rem;
  border: 1px solid #ddd; /* Placeholder */
  border-radius: 4px;
  background-color: #fff;
}
.clients-table h2 {
  margin-top: 0;
  font-size: 1.3rem;
  color: #333; /* Placeholder */
}
table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 1rem;
}
th, td {
  border: 1px solid #eee; /* Placeholder */
  padding: 0.75rem;
  text-align: left;
  font-size: 0.9em;
}
th {
  background-color: #f7f7f7; /* Placeholder */
  font-weight: bold;
}
.no-clients-message {
  text-align: center;
  color: #777; /* Placeholder */
  padding: 1rem;
}
</style>
