import { defineStore } from 'pinia'

import { ACTORS, currentActorId, setCurrentActorId } from '@/api/client'

export const useSessionStore = defineStore('session', {
  state: () => ({
    actorId: currentActorId(),
    shiftLabel: '白班 08:00-20:00',
    scope: '数据洞察分析工作台',
  }),
  getters: {
    actor: (state) => ACTORS.find((item) => item.id === state.actorId) ?? ACTORS[ACTORS.length - 1],
    operator: (state) => (ACTORS.find((item) => item.id === state.actorId) ?? ACTORS[ACTORS.length - 1]).name,
    canOperate: (state) => state.actorId.length > 0,
  },
  actions: {
    setActor(actorId: string) {
      this.actorId = actorId
      setCurrentActorId(actorId)
    },
    setShift(label: string) {
      this.shiftLabel = label
    },
  },
})
