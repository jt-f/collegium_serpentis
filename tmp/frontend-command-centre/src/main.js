// import './assets/main.css' // Default if present

import { createApp } from 'vue'
import { createPinia } from 'pinia' // Assuming Pinia is installed
import App from './App.vue'
import router from './router' // Assuming router is in src/router/index.js

const app = createApp(App)

app.use(createPinia())
app.use(router)

app.mount('#app')
