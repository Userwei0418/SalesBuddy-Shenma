const access = require('./access');
const businessOptions=require('./businessOptions');
const OPTIONS = [{value:'customer',label:'客户'},{value:'partner',label:'合作伙伴'}];
const FILTERS = [{value:'all',label:'全部身份'},...OPTIONS];
function identities(value) {
  return Array.isArray(value) ? OPTIONS.map(x=>x.value).filter(x=>value.includes(x)) : ['customer'];
}
function summary(raw = {}) {
  const values = identities(raw.business_identities);
  return {businessIdentities:values,businessIdentityText:OPTIONS.filter(x=>values.includes(x.value)).map(x=>x.label).join('、'),
    isCustomerIdentity:values.includes('customer'),isPartnerIdentity:values.includes('partner'),
    partnerProfileId:raw.partner_profile_id || '',associatedPartnerId:raw.primary_partner_id || ''};
}
function state(raw = {}) {
  return {...summary(raw),identityOptions:OPTIONS,partnerProfileName:raw.partner_profile_name || (raw.partner_profile_id ? raw.name || raw.partner_profile_id : ''),
    associatedPartnerName:raw.primary_partner_name || '',partnerAssociationChanged:false,partnerPickerVisible:false,partnerPickerQuery:'',
    partnerPickerItems:[],partnerPickerLoading:false,partnerPickerError:'',partnerPickerHasMore:false,partnerPickerOffset:0};
}
function payload(data, type) {
  const values=identities(data.businessIdentities);
  if (!values.length) throw Error('请至少选择一种业务身份');
  if (data.partnerProfileId && data.partnerProfileId===data.associatedPartnerId) throw Error('关联合作伙伴不能选择本企业自身档案');
  return {business_identities:values,customer_type:values.includes('customer')?type:null,
    ...(data.partnerProfileId ? {partner_profile_id:data.partnerProfileId} : {}),
    ...(data.partnerAssociationChanged ? {partner_id:data.associatedPartnerId || null} : {})};
}
function draft(data) {
  return {business_identities:data.businessIdentities,partner_profile_id:data.partnerProfileId,partner_profile_name:data.partnerProfileName,
    primary_partner_id:data.associatedPartnerId,primary_partner_name:data.associatedPartnerName,partnerAssociationChanged:data.partnerAssociationChanged};
}
function methods(api, environment) {
  const session=environment.session, wx=environment.wx;
  return {
    initBusinessIdentity(raw={}, saved) {
      this.initialPartnerProfileId=raw.partner_profile_id || '';
      this.setData({...state(saved || raw),identityOptions:businessOptions.businessIdentities.length?businessOptions.businessIdentities:OPTIONS,identityOptionsReady:businessOptions.businessIdentities.length===2,initialPartnerProfileId:this.initialPartnerProfileId,partnerAssociationChanged:Boolean(saved && saved.partnerAssociationChanged)});
    },
    changeBusinessIdentities(e) {
      if(this.data.submitting || this.data.saving || this.createdCustomerId)return;
      if(!this.data.identityOptionsReady)return wx().showToast({title:'业务身份选项暂不可用，请刷新后重试',icon:'none'});
      const selected=identities(e.detail.value);
      if(!selected.length){wx().showToast({title:'请至少保留一种业务身份',icon:'none'});this.setData({businessIdentities:this.data.businessIdentities});return;}
      this.setData({businessIdentities:selected,isCustomerIdentity:selected.includes('customer'),isPartnerIdentity:selected.includes('partner')});
      if (this.data.fields) this.refresh(this.data.fields);
    },
    openPartnerPicker(e) {
      if(this.data.submitting || this.data.saving || this.createdCustomerId)return;
      if(!access.can(session(),'partner.read'))return wx().showToast({title:'当前账号没有伙伴目录读取权限，请联系运营核对档案',icon:'none'});
      const mode=e.currentTarget.dataset.mode;
      if(mode==='profile' && this.initialPartnerProfileId)return;
      this.setData({partnerPickerMode:mode,partnerPickerVisible:true,partnerPickerQuery:'',partnerPickerItems:[],partnerPickerError:''});
      return this.loadIdentityPartners(false);
    },
    closePartnerPicker() {this.partnerPickerSerial=(this.partnerPickerSerial||0)+1;this.setData({partnerPickerVisible:false,partnerPickerLoading:false});},
    searchIdentityPartners(e) {this.setData({partnerPickerQuery:String(e.detail.value || '').trim()});return this.loadIdentityPartners(false);},
    moreIdentityPartners() {if(!this.data.partnerPickerLoading && this.data.partnerPickerHasMore)return this.loadIdentityPartners(true);},
    async loadIdentityPartners(append=false) {
      const serial=this.partnerPickerSerial=(this.partnerPickerSerial||0)+1, owner=access.identity(session());
      const offset=append?this.data.partnerPickerOffset:0, mode=this.data.partnerPickerMode;
      this.setData({partnerPickerLoading:true,partnerPickerError:'',...(!append?{partnerPickerItems:[]}: {})});
      try {
        const result=await api.listPartners({q:this.data.partnerPickerQuery,offset});
        if(serial!==this.partnerPickerSerial || owner!==access.identity(session()) || !this.data.partnerPickerVisible || this.unloaded)return;
        const rows=result.items;
        if(!Array.isArray(rows))throw Error('伙伴目录返回不完整，请重试');
        const items=rows.filter(row=>row.id && row.status!=='inactive' && (mode==='profile'
          ? (!row.linked_customer_id || row.linked_customer_id===this.data.customerId) && row.id!==this.data.associatedPartnerId
          : row.id!==this.data.partnerProfileId)).map(row=>({...row,description:row.linked_customer_id?'已关联企业档案':'独立伙伴档案'}));
        const next=result.next_offset===undefined ? offset+rows.length : result.next_offset;
        this.setData({partnerPickerItems:append?[...this.data.partnerPickerItems,...items]:items,
          partnerPickerHasMore:typeof result.has_more==='boolean'?result.has_more:rows.length===50,partnerPickerOffset:next});
      } catch(error) {if(serial===this.partnerPickerSerial && owner===access.identity(session()))this.setData({partnerPickerError:error.message || '伙伴目录加载失败'});}
      finally {if(serial===this.partnerPickerSerial)this.setData({partnerPickerLoading:false});}
    },
    chooseIdentityPartner(e) {
      const row=this.data.partnerPickerItems.find(x=>x.id===e.currentTarget.dataset.id);if(!row)return;
      if(this.data.partnerPickerMode==='profile')this.setData({partnerProfileId:row.id,partnerProfileName:row.name});
      else {
        this.setData({associatedPartnerId:row.id,associatedPartnerName:row.name,partnerAssociationChanged:true});
        if(this.data.fields)this.refresh(this.data.fields.map(f=>f.key==='partner_name'?{...f,value:row.name}:f));
        if(this.data.form)this.setData({'form.partner_name':row.name});
      }
      this.closePartnerPicker();
    },
    clearIdentityPartner(e) {
      if(this.data.submitting || this.data.saving || this.createdCustomerId)return;
      if(e.currentTarget.dataset.mode==='profile') {if(!this.initialPartnerProfileId)this.setData({partnerProfileId:'',partnerProfileName:''});}
      else {
        this.setData({associatedPartnerId:'',associatedPartnerName:'',partnerAssociationChanged:true});
        if(this.data.fields)this.refresh(this.data.fields.map(f=>f.key==='partner_name'?{...f,value:''}:f));
        if(this.data.form)this.setData({'form.partner_name':''});
      }
    },
  };
}
module.exports={OPTIONS,FILTERS,identities,summary,state,payload,draft,methods};
