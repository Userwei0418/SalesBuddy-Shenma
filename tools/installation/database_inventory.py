"""Value-free database fingerprints used to verify an offline restore.

Derived from the validated local recovery inventory; independent of local paths.
"""
import hashlib

from instance_files import InstanceError


def sql_name(name):
    return '"' + name.replace('"', '""') + '"'


async def table_inventory(conn):
    result = {}
    tables = await conn.fetch("""SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','p') AND n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'
        ORDER BY n.nspname,c.relname""")
    for row in tables:
        quoted = sql_name(row['nspname']) + '.' + sql_name(row['relname'])
        sha, count = hashlib.sha256(), 0
        # Stable row hashes keep business text and credentials out of the manifest.
        async for record in conn.cursor('SELECT md5(to_jsonb(t)::text) AS h FROM ' + quoted + ' t ORDER BY 1'):
            sha.update(record['h'].encode()); count += 1
        result[row['nspname'] + '.' + row['relname']] = {'rows': count, 'sha256': sha.hexdigest()}
    return result


async def security_inventory(conn):
    # Include every non-system object's stored ACL, owners and policy definitions.
    # OIDs differ between clusters; names/signatures do not. Expand default
    # ACLs: NULL means the object-specific default, not an empty grant list.
    data = {}
    data['tables'] = [dict(r) for r in await conn.fetch("""SELECT n.nspname,c.relname,c.relrowsecurity,c.relforcerowsecurity,
        pg_get_userbyid(c.relowner) AS owner,
        ARRAY(SELECT a::text FROM unnest(COALESCE(c.relacl,acldefault(
            CASE WHEN c.relkind='S' THEN 's'::\"char\" ELSE 'r'::\"char\" END,c.relowner))) a ORDER BY 1) AS acl
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2""")]
    data['functions'] = [dict(r) for r in await conn.fetch("""SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) AS args,
        p.prosecdef,p.proconfig,pg_get_userbyid(p.proowner) AS owner,
        ARRAY(SELECT a::text FROM unnest(COALESCE(p.proacl,acldefault('f',p.proowner))) a ORDER BY 1) AS acl
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2,3""")]
    data['schemas'] = [dict(r) for r in await conn.fetch("""SELECT nspname,pg_get_userbyid(nspowner) AS owner,
        ARRAY(SELECT a::text FROM unnest(COALESCE(nspacl,acldefault('n',nspowner))) a ORDER BY 1) AS acl
        FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema' ORDER BY 1""")]
    data['policies'] = [dict(r) for r in await conn.fetch('SELECT * FROM pg_policies ORDER BY schemaname,tablename,policyname')]
    data['defaults'] = [dict(r) for r in await conn.fetch("""SELECT pg_get_userbyid(d.defaclrole) AS owner,
        COALESCE(n.nspname,'') AS schema,d.defaclobjtype::text AS defaclobjtype, ARRAY(SELECT a::text FROM unnest(d.defaclacl) a ORDER BY 1) AS acl FROM pg_default_acl d
        LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace ORDER BY 1,2,3""")]
    return data


async def sequence_inventory(conn):
    return [dict(r) for r in await conn.fetch("""SELECT n.nspname,c.relname,s.seqtypid::regtype::text AS type,
        s.seqstart,s.seqincrement,s.seqmin,s.seqmax,s.seqcache,s.seqcycle FROM pg_sequence s
        JOIN pg_class c ON c.oid=s.seqrelid JOIN pg_namespace n ON n.oid=c.relnamespace ORDER BY 1,2""")]


async def verify_sequence_positions(conn):
    # Sequence counters are not MVCC snapshot data. They may safely be ahead of
    # snapshot rows, but must never generate an already restored owned-column ID.
    rows=await conn.fetch("""SELECT sn.nspname AS schema,sc.relname AS sequence,s.seqincrement,s.seqcycle,
        tn.nspname AS table_schema,tc.relname AS table_name,a.attname AS column_name
        FROM pg_sequence s JOIN pg_class sc ON sc.oid=s.seqrelid JOIN pg_namespace sn ON sn.oid=sc.relnamespace
        JOIN pg_depend d ON d.classid='pg_class'::regclass AND d.objid=sc.oid AND d.deptype IN ('a','i')
        JOIN pg_class tc ON tc.oid=d.refobjid JOIN pg_namespace tn ON tn.oid=tc.relnamespace
        JOIN pg_attribute a ON a.attrelid=tc.oid AND a.attnum=d.refobjsubid""")
    for row in rows:
        if row['seqcycle']:raise InstanceError('Cyclic owned sequences require a separate recovery check')
        counter=await conn.fetchrow('SELECT last_value,is_called FROM '+sql_name(row['schema'])+'.'+sql_name(row['sequence']))
        aggregate='max' if row['seqincrement']>0 else 'min'
        bound=await conn.fetchval('SELECT '+aggregate+'('+sql_name(row['column_name'])+') FROM '+
                                 sql_name(row['table_schema'])+'.'+sql_name(row['table_name']))
        following=counter['last_value']+(row['seqincrement'] if counter['is_called'] else 0)
        if bound is not None and ((following<=bound) if row['seqincrement']>0 else (following>=bound)):
            raise InstanceError('Restored sequence could reuse an existing ID')
    return len(rows)
