// Web-only route: all data requests use the shared authenticated API client.
Page({data:{pcRoute:true}, onLoad(options={}) { this.setData({options}); }});
