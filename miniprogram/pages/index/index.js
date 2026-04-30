const api = require("../../utils/api")
const config = require("../../utils/config")

Page({
  data: {
    ready: false,
    isWhitelisted: false,
    accounts: []
  },

  onShow() {
    this.bootstrap()
  },

  async bootstrap() {
    const app = getApp()
    if (!wx.getStorageSync("token")) {
      await app.login()
    }
    try {
      const me = await api.get("/api/me")
      this.setData({ isWhitelisted: me.is_whitelisted })
      if (me.is_whitelisted) {
        await this.loadAccounts()
      }
    } catch (err) {
      wx.showToast({ title: "加载失败", icon: "none" })
    } finally {
      this.setData({ ready: true })
    }
  },

  async loadAccounts() {
    const accounts = await api.get("/api/accounts")
    this.setData({ accounts })
  },

  async subscribe(event) {
    const accountId = event.currentTarget.dataset.id
    try {
      await api.post("/api/subscriptions", { account_id: accountId })
      await this.authorizeReminder()
      await this.loadAccounts()
      wx.showToast({ title: "已订阅" })
    } catch (err) {
      wx.showToast({ title: err.detail || "订阅失败", icon: "none" })
    }
  },

  async unsubscribe(event) {
    const accountId = event.currentTarget.dataset.id
    try {
      await api.del(`/api/subscriptions/${accountId}`)
      await this.loadAccounts()
      wx.showToast({ title: "已取消" })
    } catch (err) {
      wx.showToast({ title: "取消失败", icon: "none" })
    }
  },

  authorizeReminder() {
    if (!config.SUBSCRIBE_TEMPLATE_ID) {
      wx.showToast({ title: "请先配置模板 ID", icon: "none" })
      return Promise.resolve()
    }
    return new Promise((resolve) => {
      wx.requestSubscribeMessage({
        tmplIds: [config.SUBSCRIBE_TEMPLATE_ID],
        success: async (res) => {
          const status = res[config.SUBSCRIBE_TEMPLATE_ID] || "unknown"
          try {
            await api.post("/api/subscribe-message/authorize", {
              template_id: config.SUBSCRIBE_TEMPLATE_ID,
              status,
              available_count: status === "accept" ? 1 : 0
            })
          } catch (err) {
            wx.showToast({ title: "授权记录失败", icon: "none" })
          }
          resolve()
        },
        fail: () => {
          wx.showToast({ title: "授权失败", icon: "none" })
          resolve()
        }
      })
    })
  }
})

