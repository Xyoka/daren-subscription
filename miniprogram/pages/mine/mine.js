const api = require("../../utils/api")

Page({
  data: {
    me: null
  },

  onShow() {
    this.reload()
  },

  async reload() {
    try {
      const me = await api.get("/api/me")
      this.setData({ me })
    } catch (err) {
      wx.showToast({ title: "加载失败", icon: "none" })
    }
  }
})

