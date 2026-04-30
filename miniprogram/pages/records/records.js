const api = require("../../utils/api")

Page({
  data: {
    records: []
  },

  onShow() {
    this.load()
  },

  async load() {
    try {
      const records = await api.get("/api/push-records")
      this.setData({ records })
    } catch (err) {
      wx.showToast({ title: "加载失败", icon: "none" })
    }
  },

  openPost(event) {
    wx.navigateTo({ url: `/pages/post/post?id=${event.currentTarget.dataset.id}` })
  }
})

