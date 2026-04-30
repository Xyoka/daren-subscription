const api = require("./utils/api")

App({
  globalData: {
    user: null
  },

  onLaunch() {
    return this.login()
  },

  login() {
    return new Promise((resolve) => {
      wx.login({
        success: async (res) => {
          try {
            const data = await api.post("/api/wechat/login", { code: res.code })
            wx.setStorageSync("token", data.token)
            this.globalData.user = data
            resolve(data)
          } catch (err) {
            wx.showToast({ title: "登录失败", icon: "none" })
            resolve(null)
          }
        },
        fail: () => {
          wx.showToast({ title: "微信登录失败", icon: "none" })
          resolve(null)
        }
      })
    })
  }
})

