const api = require("./utils/api")

App({
  globalData: {
    user: null
  },

  onLaunch() {
    return this.login()
  },

  getDeviceId() {
    let deviceId = wx.getStorageSync("device_id")
    if (!deviceId) {
      // 生成一个稳定的设备 ID，开发模式下保证同一设备登录同一用户
      const chars = "abcdefghijklmnopqrstuvwxyz0123456789"
      deviceId = "dev_"
      for (let i = 0; i < 16; i++) {
        deviceId += chars[Math.floor(Math.random() * chars.length)]
      }
      wx.setStorageSync("device_id", deviceId)
    }
    return deviceId
  },

  login() {
    return new Promise((resolve) => {
      wx.login({
        success: async (res) => {
          try {
            const data = await api.post("/api/wechat/login", {
              code: res.code,
              dev_openid: this.getDeviceId()
            })
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

