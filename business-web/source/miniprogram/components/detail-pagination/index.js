Component({properties:{state:{type:Object,value:{}},emptyText:{type:String,value:'暂无记录'}},methods:{more(){this.triggerEvent('more');},retry(){this.triggerEvent('retry');}}});
