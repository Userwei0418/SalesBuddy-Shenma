/* Web detail navigation and viewport behavior; writes use the shared forms/APIs. */
(function(global){
  function sessionKey(){
    const s=global.SalesRuntime?.app?.globalData?.session;
    return s ? [s.workspaceId,s.userId,s.role,s.permissionVersion||'',s.loginAt||''].join(':') : '';
  }
  function canEditOpportunity(page){
    const s=global.SalesRuntime?.app?.globalData?.session,d=page.data,o=d.opportunity;
    // Same capability/ownership rule as the shared opportunity list. FDE-list
    // maintenance and readonly (actuals) are separate permissions.
    return Boolean((s?.permissions ? s.permissions['opportunity.update']===true && o?.can_edit===true : s?.capabilities?.['opportunity.edit']===true) && !d.accessBlocked
      && !d.opportunityLoading && !d.opportunityError && d.customerId && d.opportunityId
      && o?.id===d.opportunityId && (!o.customer_id || o.customer_id===d.customerId)
      && (s.permissions || s.role!=='sales' || (s.userId && o.owner_id===s.userId)));
  }
  function configureOpportunity(page){
    page.data.webCanEditOpportunity=canEditOpportunity(page);
    if(page._webOpportunityEditConfigured)return;
    page._webOpportunityEditConfigured=true;
    page.webEditOpportunity=function(){
      const runtime=global.SalesRuntime,d=this.data;
      if(runtime.current!==this || !canEditOpportunity(this) || d.fdeRelationSaving || this._webEditConfirming)return;
      const context={identity:sessionKey(),customerId:d.customerId,opportunityId:d.opportunityId};
      const open=()=>{
        const current=this.data;
        if(runtime.current!==this || context.identity!==sessionKey() || !canEditOpportunity(this)
          || current.customerId!==context.customerId || current.opportunityId!==context.opportunityId || current.fdeRelationSaving)return;
        this._webEditingOpportunity=context;
        runtime.wx.navigateTo({url:'/pages/opportunity-create/index?customerId='+encodeURIComponent(context.customerId)
          +'&opportunityId='+encodeURIComponent(context.opportunityId)});
      };
      if(!d.fdeRelationDirty)return open();
      this._webEditConfirming=true;
      runtime.wx.showModal({title:'协助名单尚未保存',content:'继续编辑商机将放弃当前未保存的名单调整。也可以取消，先保存协助名单。',
        confirmText:'继续编辑',cancelText:'取消',success:result=>{
          this._webEditConfirming=false;if(result.confirm)open();
        },fail:()=>{this._webEditConfirming=false;}});
    };
    const onShow=page.onShow;
    page.onShow=function(...args){
      const context=this._webEditingOpportunity,wx=global.SalesRuntime.wx;
      // A save returning to this detail already refreshes it through native
      // onShow. Consume only this editor's matching redirect receipt.
      if(context && context.identity===sessionKey() && context.customerId===this.data.customerId
        && context.opportunityId===this.data.opportunityId
        && wx.getStorageSync('pendingOpenCustomerId')===context.customerId
        && wx.getStorageSync('pendingOpenOpportunityId')===context.opportunityId){
        wx.removeStorageSync('pendingOpenCustomerId');wx.removeStorageSync('pendingOpenOpportunityId');
      }
      this._webEditingOpportunity=null;
      return onShow?.apply(this,args);
    };
  }
  function configure(page){
    if(page.route==='pages/customer-assets/index')configureOpportunity(page);
    if(page._webDetailConfigured)return;
    const handler={
      'pages/customers/index':'selectDetailTab',
      'pages/customer-detail/index':'selectTab',
      'pages/customer-assets/index':'selectOpportunityTab',
    }[page.route];
    if(!handler||typeof page[handler]!=='function')return;
    page._webDetailConfigured=true;
    const original=page[handler];
    page[handler]=function(event){
      const result=original.call(this,event);
      if(matchMedia('(min-width:601px)').matches){
        const selectors=page.route==='pages/customers/index'
          ? '.web-customer-workspace .web-customer-primary,.web-customer-workspace .detail-scroll,.web-customer-workspace .web-detail-columns,.web-customer-workspace .web-detail-main,.web-customer-workspace .web-detail-aside'
          : page.route==='pages/customer-assets/index'
          ? '.web-op-workarea,.web-op-main,.web-op-columns,#page-root'
          : '#page-root,.web-standalone-customer .web-customer-primary,.web-standalone-customer .web-detail-columns,.web-standalone-customer .web-detail-main,.web-standalone-customer .web-detail-aside';
        document.querySelectorAll(selectors).forEach(scroll=>scroll.scrollTo({top:0,behavior:'instant'}));
      }
      return result;
    };
  }
  function scrollContainer(container,target){
    if(!matchMedia('(min-width:601px)').matches || !container.matches('.web-customer-workspace .detail-scroll'))return container;
    const primary=container.closest('.web-customer-primary');
    if(primary && /auto|scroll/.test(getComputedStyle(primary).overflowY))return primary;
    for(let node=target.parentElement;node && node!==container;node=node.parentElement){
      if(node.matches('.web-detail-main,.web-detail-columns') && /auto|scroll/.test(getComputedStyle(node).overflowY))return node;
    }
    return container;
  }
  global.SalesDetailWorkspace=Object.freeze({configure,scrollContainer});
})(globalThis);
