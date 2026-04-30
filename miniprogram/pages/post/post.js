const api = require("../../utils/api")

Page({
  data: {
    post: null
  },

  onLoad(options) {
    this.load(options.id)
  },

  async load(id) {
    try {
      const post = await api.get(`/api/posts/${id}`)
      this.setData({ post })
    } catch (err) {
      wx.showToast({ title: "内容不可访问", icon: "none" })
    }
  },

  copyLink() {
    if (!this.data.post) return
    wx.setClipboardData({
      data: this.data.post.original_url,
      success: () => wx.showToast({ title: "已复制" })
    })
  }
})

