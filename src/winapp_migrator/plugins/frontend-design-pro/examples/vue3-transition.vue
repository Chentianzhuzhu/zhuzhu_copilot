<template>
  <div class="app">
    <button @click="currentState = 'home'">Home</button>
    <button @click="currentState = 'about'">About</button>
    
    <transition name="fade" mode="out-in">
      <component :is="currentComponent" :key="currentState" />
    </transition>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const currentState = ref('home')

const components = {
  home: { template: '<div class="panel"><h2>Home Page</h2><p>Welcome to the home page with fade animation!</p></div>' },
  about: { template: '<div class="panel"><h2>About Page</h2><p>This is the about page with smooth transition!</p></div>' }
}

const currentComponent = computed(() => components[currentState.value] || components.home)
</script>

<style scoped>
.app {
  padding: 20px;
}

button {
  padding: 10px 20px;
  margin-right: 10px;
  cursor: pointer;
}

.panel {
  padding: 40px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border-radius: 10px;
  margin-top: 20px;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.4s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>