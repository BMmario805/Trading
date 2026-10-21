"""Un intento OCI acotado por ejecución; por defecto solo imprime el plan.

Con --apply requiere el SDK oficial `oci` y credenciales locales. No migra
estado ni arranca el motor. Un planificador puede ejecutarlo cada 15 minutos;
el diario persistente impide duplicados y detiene respuestas inciertas.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
import uuid


def validate(plan):
    if plan.get('region')!='eu-madrid-1' or not plan.get('displayName'):
        raise ValueError('Se requiere región Madrid y nombre explícitos')
    requests=plan.get('requests',[])
    if not 1<=len(requests)<=2: raise ValueError('Uno o dos shapes permitidos')
    for request in requests:
        shape=request['shape']
        if shape=='VM.Standard.A1.Flex':
            if request.get('architecture')!='aarch64' or request.get('shapeConfig')!={'ocpus':1,'memoryInGBs':6}:
                raise ValueError('A1 requiere imagen ARM y configuración 1 OCPU / 6 GB')
        elif shape=='VM.Standard.E2.1.Micro':
            if request.get('architecture')!='x86_64' or request.get('shapeConfig'):
                raise ValueError('Micro requiere imagen AMD64 y sin shapeConfig flexible')
        else: raise ValueError('Shape fuera del plan acotado')
        for key,kind in [('imageId','image'),('subnetId','subnet')]:
            if not request.get(key,'').startswith('ocid1.'+kind+'.'):
                raise ValueError('Falta identificador OCI válido: '+key)
        if not request.get('availabilityDomain'): raise ValueError('Falta dominio de disponibilidad')
    if not plan.get('compartmentId','').startswith('ocid1.compartment.'):
        raise ValueError('Falta compartimento OCI')
    if not plan.get('sshPublicKey','').startswith(('ssh-ed25519 ','ssh-rsa ')):
        raise ValueError('Falta clave SSH pública')
    return requests


def is_capacity_error(exc):
    return (getattr(exc,'status',None)==500 and getattr(exc,'code',None)=='InternalError'
            and any(text in str(getattr(exc,'message','')).lower() for text in ('out of host capacity','out of capacity')))


def attempt(plan,journal,oci,profile='DEFAULT',now=None):
    requests=validate(plan)
    now=time.time() if now is None else now
    config=oci.config.from_file(profile_name=profile)
    config['region']=plan['region']
    client=oci.core.ComputeClient(config,retry_strategy=oci.retry.NoneRetryStrategy())
    journal=Path(journal)
    journal.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(journal,timeout=30)) as con, con:
        con.execute('CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,ts REAL,status TEXT,shape_index INTEGER,token TEXT,instance_id TEXT)')
        con.execute('BEGIN IMMEDIATE')
        previous=con.execute('SELECT ts,status,shape_index,token FROM attempts ORDER BY id DESC LIMIT 1').fetchone()
        if previous and previous[1] in ('pending','uncertain','created'):
            return dict(status=previous[1],message='Reconciliar el intento guardado antes de crear otro recurso')
        if previous and now-previous[0]<900:
            return dict(status='backoff',message='Esperar al menos quince minutos entre intentos')
        instances=oci.pagination.list_call_get_all_results(client.list_instances,
            compartment_id=plan['compartmentId'],display_name=plan['displayName']).data
        if any(instance.lifecycle_state!='TERMINATED' for instance in instances):
            return dict(status='exists',message='La instancia ya existe; no se crea otra')
        index=((previous[2]+1)%len(requests)) if previous else 0
        request=requests[index]
        token=uuid.uuid4().hex
        row=con.execute('INSERT INTO attempts(ts,status,shape_index,token) VALUES (?,?,?,?)',(now,'pending',index,token)).lastrowid
        con.commit()  # Persistir intención antes de la llamada de creación.
        options=dict(availability_domain=request['availabilityDomain'],compartment_id=plan['compartmentId'],
            display_name=plan['displayName'],shape=request['shape'],
            create_vnic_details=oci.core.models.CreateVnicDetails(subnet_id=request['subnetId'],assign_public_ip=True),
            metadata={'ssh_authorized_keys':plan['sshPublicKey']},
            source_details=oci.core.models.InstanceSourceViaImageDetails(image_id=request['imageId'],boot_volume_size_in_gbs=50))
        if request.get('shapeConfig'):
            options['shape_config']=oci.core.models.LaunchInstanceShapeConfigDetails(ocpus=1,memory_in_gbs=6)
        try:
            response=client.launch_instance(oci.core.models.LaunchInstanceDetails(**options),opc_retry_token=token)
            instance_id=response.data.id
            con.execute('UPDATE attempts SET status=?,instance_id=? WHERE id=?',('created',instance_id,row))
            return dict(status='created',instanceId=instance_id,shape=request['shape'],message='Instancia creada; migración y arranque pendientes')
        except Exception as exc:
            status='capacity' if is_capacity_error(exc) else 'uncertain'
            con.execute('UPDATE attempts SET status=? WHERE id=?',(status,row))
            return dict(status=status,message='Sin capacidad; el siguiente turno alternará shape' if status=='capacity'
                else 'Respuesta no confirmada: detener reintentos y reconciliar en Oracle')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--journal',type=Path,default=Path('.runtime/oracle-retry.sqlite'))
    parser.add_argument('--profile',default='DEFAULT')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    plan=json.loads(args.plan.read_text(encoding='utf-8-sig'))
    requests=validate(plan)
    if not args.apply:
        result=dict(status='plan',region=plan['region'],shapes=[r['shape'] for r in requests],
                    maxInstances=1,bootVolumeGB=50,intervalSeconds=900,
                    message='Sin llamadas al proveedor; revisar cuota, imágenes y coste antes de --apply')
    else:
        import oci
        result=attempt(plan,args.journal,oci,args.profile)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__': main()
