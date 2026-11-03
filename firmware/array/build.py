"""Build array firmware from source with the recorded compiler and linker flags."""
from pathlib import Path
import argparse, base64, csv, hashlib, os, shutil, struct, subprocess

ROOT=Path(__file__).resolve().parent

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def elf_images(path):
    data=path.read_bytes()
    if data[:7]!=b'\x7fELF\x01\x01\x01':raise ValueError('Expected ELF32 little-endian firmware')
    phoff=struct.unpack_from('<I',data,28)[0]
    size,count=struct.unpack_from('<HH',data,42)
    rom=bytearray(b'\x13\0\0\0'*(65536//4));ram=bytearray(b'\xab'*65536)
    for i in range(count):
        kind,offset,vaddr,paddr,filesz,memsz,flags,align=struct.unpack_from('<8I',data,phoff+i*size)
        if kind!=1 or not filesz:continue
        if 0<=vaddr and vaddr+memsz<=len(rom):rom[vaddr:vaddr+filesz]=data[offset:offset+filesz]
        elif 0x10000000<=vaddr and vaddr+memsz<=0x10000000+len(ram):
            start=vaddr-0x10000000;ram[start:start+filesz]=data[offset:offset+filesz]
        else:raise ValueError('ELF load segment outside selected memory map')
    return {name:''.join(f'{int.from_bytes(blob[i:i+4],"little"):08x}\r\n' for i in range(0,len(blob),4)).encode() for name,blob in [('imem.hex',rom),('dmem.hex',ram)]}

def load_recipe():
    commands=[]
    with (ROOT/'build_commands.csv').open(encoding='utf-8',newline='') as handle:
        rows=csv.reader(handle)
        if next(rows,None)!=['step','argument','utf8_base64url']:
            raise ValueError('Invalid command table header')
        for number,row in enumerate(rows,2):
            if len(row)!=3:raise ValueError('Invalid command table row '+str(number))
            step,argument=int(row[0]),int(row[1])
            if step==len(commands):
                if argument!=0:raise ValueError('First command argument must be zero')
                commands.append([])
            elif step!=len(commands)-1:raise ValueError('Commands must be contiguous and ordered')
            if step<0 or argument!=len(commands[step]):raise ValueError('Arguments must be contiguous and ordered')
            value=base64.b64decode(row[2].encode('ascii'),altchars=b'-_',validate=True).decode('utf-8')
            commands[step].append(value)
    if not commands:raise ValueError('No compiler commands')
    return commands

def read_checksums():
    manifest={}
    for number,line in enumerate((ROOT/'SHA256SUMS').read_text(encoding='utf-8').split('\n'),1):
        if not line:continue
        escaped=line.startswith('\\')
        if escaped:line=line[1:]
        if len(line)<67 or line[64:66]!='  ':raise ValueError('Invalid checksum row '+str(number))
        digest,name=line[:64],line[66:]
        if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('Invalid SHA256 digest')
        if escaped:
            result=[];i=0
            while i<len(name):
                if name[i]!='\\':result.append(name[i]);i+=1;continue
                i+=1
                if i>=len(name) or name[i] not in {'\\','n','r'}:raise ValueError('Invalid checksum filename escape')
                result.append({'\\':'\\','n':'\n','r':'\r'}[name[i]]);i+=1
            name=''.join(result)
        if not name or name in manifest or not (ROOT/name).resolve().is_relative_to(ROOT.resolve()):raise ValueError('Invalid or duplicate checksum path')
        manifest[name]=digest
    return manifest

def check():
    manifest=read_checksums()
    for name,digest in manifest.items():
        p=ROOT/name
        if not p.is_file() or sha(p)!=digest:raise ValueError('File changed or missing: '+name)
    actual={p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*') if p.is_file() and p.name!='SHA256SUMS' and not p.is_relative_to(ROOT/'build') and '__pycache__' not in p.parts}
    if set(manifest)!=actual:raise ValueError('Source package file list differs from SHA256SUMS')
    recipe=load_recipe()
    produced=set()
    for c in recipe:
        output=c[c.index('-o')+1] if '-o' in c else c[-1]
        outputs=[output]
        for i,v in enumerate(c):
            if v=='-MF':outputs.append(c[i+1])
            if v.startswith('-MF') and v!='-MF':outputs.append(v[3:])
            if v.startswith('-Wl,'):
                outputs.extend(part.split('=',1)[1] for part in v.split(',') if part.startswith('-Map='))
        for name in outputs:
            target=(ROOT/name).resolve()
            build=(ROOT/'build').resolve()
            if not target.is_relative_to(build) or target==build:
                raise ValueError('Build output must stay inside build/: '+name)
            if name in manifest:raise ValueError('Build output overlaps a submitted input: '+name)
            if target.exists() and target.is_dir():raise ValueError('Output is a directory: '+name)
            for parent in target.parents:
                if parent==ROOT:break
                if parent.exists() and not parent.is_dir():raise ValueError('Output parent cannot be created: '+name)
        for i,v in enumerate(c[1:],1):
            if v.startswith('{toolchain}'):continue
            if v.endswith(('.o','.c','.cc','.cpp','.S','.s','.ld')) and v!=output:
                if v not in produced and not (ROOT/v).is_file():raise ValueError('Unresolved build input '+v)
        produced.add(output)
    print('PASS: source hashes, compiler input chain, and all outputs confined to build/. No generated artifacts required.')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check',action='store_true',help='Check only; no compiler, hardware or network access')
    p.add_argument('--toolchain',help='Directory containing riscv-none-elf-gcc.exe, g++.exe and objcopy.exe')
    args=p.parse_args()
    check()
    if args.check:return
    directory=args.toolchain or os.environ.get('RISCV_TOOLCHAIN')
    if not directory:
        found=shutil.which('riscv-none-elf-gcc.exe') or shutil.which('riscv-none-elf-gcc')
        directory=str(Path(found).parent) if found else None
    if not directory:raise SystemExit('Set --toolchain or RISCV_TOOLCHAIN to xPack RISC-V GCC 15.2.0-1 bin directory.')
    tc=Path(directory)
    recipe=load_recipe()
    (ROOT/'build').mkdir(exist_ok=True)
    for i,c in enumerate(recipe,1):
        command=[str(tc/v.split('/',1)[1]) if v.startswith('{toolchain}/') else v for v in c]
        print(f'[{i}/{len(recipe)}] {Path(command[0]).name}',flush=True)
        log=ROOT/'build'/f'{i:02d}.log'
        with log.open('wb') as handle:
            result=subprocess.run(command,cwd=ROOT,stdout=handle,stderr=subprocess.STDOUT,creationflags=0x08000000 if os.name=='nt' else 0)
        if result.returncode:raise SystemExit(f'Build failed; see {log}')
    images=ROOT/'build/images';images.mkdir(exist_ok=True)
    generated=elf_images(ROOT/'build/firmware.elf')
    for name,data in generated.items():
        (images/name).write_bytes(data)
        words=(images/name).read_text(encoding='ascii').splitlines()
        recovered=b''.join(int(word,16).to_bytes(4,'little') for word in words)
        if len(words)!=16384 or len(recovered)!=65536:raise ValueError('Image size mismatch: '+name)
        encoded=''.join(f'{int.from_bytes(recovered[i:i+4],"little"):08x}\r\n' for i in range(0,len(recovered),4)).encode('ascii')
        if encoded!=data:raise ValueError('Image roundtrip mismatch: '+name)
    (ROOT/'build/BUILD_REPORT.txt').write_text('status=COMPILED_ELF_HEX_STATIC_CHECKS_PASS\nelf_sha256='+sha(ROOT/'build/firmware.elf')+'\nimem_sha256='+sha(images/'imem.hex')+'\ndmem_sha256='+sha(images/'dmem.hex')+'\nelf_to_hex_roundtrip=true\nboard_execution=NOT_PERFORMED\nFPGA_bitstream=NOT_GENERATED\n',encoding='utf-8',newline='\n')
    print('Build complete: build/firmware.elf, build/firmware.map, and build/images/*.hex. ELF/HEX static checks passed.')

if __name__=='__main__':main()
