const config = require("./config")

function request(method, path, data = {}) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${config.API_BASE_URL}${path}`,
      method,
      data,
      header: {
        "content-type": "application/json",
        Authorization: wx.getStorageSync("token") ? `Bearer ${wx.getStorageSync("token")}` : ""
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
          return
        }
        reject(res.data || { detail: "request failed" })
      },
      fail: reject
    })
  })
}

module.exports = {
  get: (path) => request("GET", path),
  post: (path, data) => request("POST", path, data),
  del: (path) => request("DELETE", path)
}

